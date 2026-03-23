"""
API endpoint: Upload a travel PDF → AI agent creates a full itinerary.

Async flow:
  POST /api/import-pdf/       → returns { task_id }
  GET  /api/import-pdf/<id>/  → returns { status, collection_id, error }
"""

import json
import logging
import os
import threading
import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from adventures.models import (
    Category, Checklist, ChecklistItem, Collection, ContentAttachment, ContentImage,
    CollectionItineraryDay, CollectionItineraryItem,
    Location, Lodging, Note, Transportation,
)
from adventures.serializers import CollectionSerializer

User = get_user_model()
logger = logging.getLogger(__name__)

# In-memory task store (survives across requests within the same gunicorn worker)
_tasks = {}  # task_id -> { status: pending|running|done|error, collection_id, error }


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using PyMuPDF or pdfplumber."""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except ImportError:
        pass
    try:
        import pdfplumber
        import io
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        return ""


def _extract_pdf_images(pdf_bytes: bytes, min_size: int = 10000) -> list:
    """Extract images from PDF bytes using PyMuPDF. Returns list of (image_bytes, ext)."""
    images = []
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            for img_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                base_image = doc.extract_image(xref)
                if base_image and len(base_image["image"]) >= min_size:
                    ext = base_image.get("ext", "png")
                    images.append((base_image["image"], ext, page_num))
        doc.close()
    except Exception:
        pass
    return images


def _auto_generate_itinerary(collection):
    """Create itinerary days and assign dated items that the agent didn't already schedule."""
    from datetime import timedelta
    from django.db.models import Max

    if not collection.start_date or not collection.end_date:
        return

    current = collection.start_date
    day_num = 1
    while current <= collection.end_date:
        CollectionItineraryDay.objects.get_or_create(
            collection=collection, date=current,
            defaults={'name': f'Day {day_num}'}
        )
        current += timedelta(days=1)
        day_num += 1

    def _next_order(d):
        """Get the next available order number for a given date."""
        max_order = CollectionItineraryItem.objects.filter(
            collection=collection, date=d
        ).aggregate(Max('order'))['order__max']
        return (max_order or 0) + 1

    for t in Transportation.objects.filter(collection=collection, date__isnull=False):
        d = t.date.date() if hasattr(t.date, 'date') else t.date
        ct = ContentType.objects.get_for_model(Transportation)
        if not CollectionItineraryItem.objects.filter(collection=collection, content_type=ct, object_id=t.id).exists():
            CollectionItineraryItem.objects.create(
                collection=collection, content_type=ct, object_id=t.id,
                date=d, order=_next_order(d)
            )

    for l in Lodging.objects.filter(collection=collection, check_in__isnull=False):
        d = l.check_in.date() if hasattr(l.check_in, 'date') else l.check_in
        ct = ContentType.objects.get_for_model(Lodging)
        if not CollectionItineraryItem.objects.filter(collection=collection, content_type=ct, object_id=l.id).exists():
            CollectionItineraryItem.objects.create(
                collection=collection, content_type=ct, object_id=l.id,
                date=d, order=_next_order(d)
            )

    for n in Note.objects.filter(collection=collection, date__isnull=False):
        d = n.date.date() if hasattr(n.date, 'date') else n.date
        ct = ContentType.objects.get_for_model(Note)
        if not CollectionItineraryItem.objects.filter(collection=collection, content_type=ct, object_id=n.id).exists():
            CollectionItineraryItem.objects.create(
                collection=collection, content_type=ct, object_id=n.id,
                date=d, order=_next_order(d)
            )

    # Add undated notes and checklists as global/trip-wide context items
    global_order = 1
    for n in Note.objects.filter(collection=collection, date__isnull=True):
        ct = ContentType.objects.get_for_model(Note)
        if not CollectionItineraryItem.objects.filter(collection=collection, content_type=ct, object_id=n.id).exists():
            CollectionItineraryItem.objects.create(
                collection=collection, content_type=ct, object_id=n.id,
                is_global=True, order=global_order
            )
            global_order += 1

    for cl in Checklist.objects.filter(collection=collection):
        ct = ContentType.objects.get_for_model(Checklist)
        if not CollectionItineraryItem.objects.filter(collection=collection, content_type=ct, object_id=cl.id).exists():
            CollectionItineraryItem.objects.create(
                collection=collection, content_type=ct, object_id=cl.id,
                is_global=True, order=global_order
            )
            global_order += 1


def _run_agent(pdf_text, user, pdf_filename, pdf_bytes, task_id):
    """Run the Strands agent in a background thread."""
    import django
    django.setup()

    from strands import Agent, tool
    from strands.models import BedrockModel

    # Extract images from the PDF for the agent to use
    pdf_images = _extract_pdf_images(pdf_bytes)

    ctx = {'user': user, 'collection': None, 'pdf_images': pdf_images}

    try:
        _tasks[task_id]['status'] = 'running'

        def _progress(msg):
            _tasks[task_id]['progress'].append(msg)

        @tool
        def create_trip(name: str, description: str, start_date: str, end_date: str,
                        link: str = "") -> str:
            """Create a new trip collection.
            Args:
                name: Trip name
                description: Brief description (3-5 sentences about the trip)
                start_date: YYYY-MM-DD
                end_date: YYYY-MM-DD
                link: URL to the tour operator or booking page if found in the PDF (optional)
            """
            collection = Collection.objects.create(
                user=ctx['user'], name=name, description=description,
                start_date=date.fromisoformat(start_date),
                end_date=date.fromisoformat(end_date), is_public=False,
                link=link or None,
            )
            ctx['collection'] = collection
            _tasks[task_id]['collection_id'] = str(collection.id)
            _progress(f"✈️ Created trip: {name}")
            return json.dumps({'id': str(collection.id), 'name': collection.name})

        @tool
        def add_location(name: str, description: str, latitude: float, longitude: float,
                         category: str = "general", category_icon: str = "🌍",
                         english_name: str = "", location_display: str = "",
                         tags: list = [], link: str = "") -> str:
            """Add a destination to the trip. If the user already has a location with the same name nearby, it will be linked to this trip instead of creating a duplicate.
            Args:
                name: Place name in the PDF's language (displayed to user)
                description: What happens here (2-3 sentences)
                latitude: Lat coordinate
                longitude: Lng coordinate
                category: Category name like: national_park, city, lake, lodge, restaurant, airport, wetland, viewpoint, wildlife, cultural, general
                category_icon: Emoji icon for the category (e.g. 🏞️ for parks, 🏙️ for cities, 🌊 for lakes, 🍽️ for restaurants, ✈️ for airports, 🦁 for wildlife, 🏛️ for cultural)
                english_name: The well-known ENGLISH name for geocoding (e.g. 'Cape of Good Hope' not 'Kap der Guten Hoffnung'). Use the common/short name that Google Maps would recognize.
                location_display: Human-readable location string (e.g. "Swakopmund, Namibia" or "Cape Town, South Africa"). This is shown as the subtitle under the location name.
                tags: List of descriptive tags (e.g. ["safari", "wildlife", "national_park"] or ["beach", "surfing"]) (optional)
                link: URL to the place's official website or relevant page (optional)
            """
            # Get or create the category for this user
            cat, _ = Category.objects.get_or_create(
                user=ctx['user'], name=category.lower().strip(),
                defaults={'display_name': category.replace('_', ' ').title(), 'icon': category_icon}
            )
            # Geocode using the English name for better results, with progressive shortening
            geocode_name = english_name or name
            lat, lng = latitude, longitude
            from adventures.geocoding import search as geo_search
            geocode_display = None
            try:
                results = geo_search(geocode_name)
                # If no results, try progressively shorter names
                if (not isinstance(results, list) or not results) and geocode_name:
                    words = geocode_name.split()
                    for drop in range(1, min(len(words), 4)):
                        shorter = ' '.join(words[:-drop])
                        if len(shorter) < 3:
                            break
                        results = geo_search(shorter)
                        if isinstance(results, list) and results:
                            break
                if isinstance(results, list) and results:
                    lat = float(results[0].get('lat', latitude))
                    lng = float(results[0].get('lon', longitude))
                    # Use the geocoder's display_name as fallback for location_display
                    geocode_display = results[0].get('display_name', '')
            except Exception:
                pass  # Fall back to AI-provided coordinates

            # Check if user already has a location with the same name (reuse instead of duplicate)
            from django.db.models import Q as DQ
            existing_loc = Location.objects.filter(
                DQ(user=ctx['user']),
                DQ(name__iexact=name) | DQ(name__iexact=english_name) if english_name else DQ(name__iexact=name)
            ).first()

            if existing_loc:
                # Link existing location to this collection
                if ctx['collection']:
                    existing_loc.collections.add(ctx['collection'])
                _progress(f"🔗 Linked existing location: {existing_loc.name} [{category}]")
                return json.dumps({'id': str(existing_loc.id), 'name': existing_loc.name, 'reused': True})

            # Determine the display location string
            display_loc = location_display or geocode_display or ""

            loc = Location(user=ctx['user'], name=name, description=description,
                           latitude=lat, longitude=lng, category=cat,
                           location=display_loc,
                           tags=tags if tags else None,
                           link=link or None)
            loc.save(_skip_geocode=False)
            if ctx['collection']:
                loc.collections.add(ctx['collection'])
            _progress(f"📍 Added location: {name} [{category}]")
            return json.dumps({'id': str(loc.id), 'name': loc.name, 'reused': False})

        @tool
        def add_transportation(name: str, transport_type: str, from_location: str,
                               to_location: str, date: str, end_date: str = "",
                               flight_number: str = "", description: str = "",
                               from_code: str = "", to_code: str = "",
                               from_latitude: float = 0, from_longitude: float = 0,
                               to_latitude: float = 0, to_longitude: float = 0,
                               link: str = "", price: str = "") -> str:
            """Add a transport leg (flight, bus, car, etc.) with full geocoding.
            The tool will geocode from/to locations to get exact coordinates, airport/station codes, and timezones.
            Args:
                name: Transport name (e.g. "Frankfurt to Johannesburg" or "Windhoek to Swakopmund")
                transport_type: car, plane, train, bus, boat, bike, walking, other
                from_location: Origin city/airport name (e.g. "Frankfurt Airport" or "Windhoek")
                to_location: Destination city/airport name (e.g. "Johannesburg Airport" or "Swakopmund")
                date: Departure YYYY-MM-DD
                end_date: Arrival YYYY-MM-DD (optional)
                flight_number: Flight number if known (e.g. "LH572") — also use for train numbers, bus numbers, etc. (optional)
                description: Details (optional)
                from_code: Origin IATA/station code if known (e.g. "FRA", "JNB", "WDH") (optional)
                to_code: Destination IATA/station code if known (e.g. "JNB", "WDH") (optional)
                from_latitude: Origin latitude if known (optional, will be geocoded if not provided)
                from_longitude: Origin longitude if known (optional, will be geocoded if not provided)
                to_latitude: Destination latitude if known (optional, will be geocoded if not provided)
                to_longitude: Destination longitude if known (optional, will be geocoded if not provided)
                link: URL to booking confirmation or airline/operator website (optional)
                price: Price as a string e.g. "350.00" in the trip's currency (optional)
            """
            valid_types = {'car', 'plane', 'train', 'bus', 'boat', 'bike', 'walking', 'other'}
            if transport_type not in valid_types:
                transport_type = 'other'

            from adventures.geocoding import search as geo_search

            # Geocode origin
            origin_lat, origin_lng = from_latitude, from_longitude
            start_code = from_code.strip().upper() if from_code else None
            if not origin_lat or not origin_lng:
                try:
                    query = f"{from_location} Airport" if transport_type == 'plane' else from_location
                    results = geo_search(query)
                    if isinstance(results, list) and results:
                        origin_lat = float(results[0].get('lat', 0))
                        origin_lng = float(results[0].get('lon', 0))
                        # Try to extract airport code from result name (e.g. "Frankfurt Airport (FRA)")
                        if not start_code and transport_type == 'plane':
                            rname = results[0].get('name', '')
                            import re
                            code_match = re.search(r'\(([A-Z]{3})\)', rname)
                            if code_match:
                                start_code = code_match.group(1)
                except Exception:
                    pass

            # Geocode destination
            dest_lat, dest_lng = to_latitude, to_longitude
            end_code = to_code.strip().upper() if to_code else None
            if not dest_lat or not dest_lng:
                try:
                    query = f"{to_location} Airport" if transport_type == 'plane' else to_location
                    results = geo_search(query)
                    if isinstance(results, list) and results:
                        dest_lat = float(results[0].get('lat', 0))
                        dest_lng = float(results[0].get('lon', 0))
                        if not end_code and transport_type == 'plane':
                            rname = results[0].get('name', '')
                            import re
                            code_match = re.search(r'\(([A-Z]{3})\)', rname)
                            if code_match:
                                end_code = code_match.group(1)
                except Exception:
                    pass

            # Derive timezones from coordinates using Google Maps Time Zone API or fallback
            start_tz, end_tz = None, None
            try:
                from django.conf import settings as django_settings
                import requests as req
                gmap_key = getattr(django_settings, 'GOOGLE_MAPS_API_KEY', None)
                if gmap_key and origin_lat and origin_lng:
                    import time as _time
                    ts = int(_time.time())
                    tz_resp = req.get(
                        f"https://maps.googleapis.com/maps/api/timezone/json?location={origin_lat},{origin_lng}&timestamp={ts}&key={gmap_key}",
                        timeout=5
                    )
                    if tz_resp.status_code == 200:
                        tz_data = tz_resp.json()
                        if tz_data.get('status') == 'OK':
                            start_tz = tz_data.get('timeZoneId')
                if gmap_key and dest_lat and dest_lng:
                    import time as _time
                    ts = int(_time.time())
                    tz_resp = req.get(
                        f"https://maps.googleapis.com/maps/api/timezone/json?location={dest_lat},{dest_lng}&timestamp={ts}&key={gmap_key}",
                        timeout=5
                    )
                    if tz_resp.status_code == 200:
                        tz_data = tz_resp.json()
                        if tz_data.get('status') == 'OK':
                            end_tz = tz_data.get('timeZoneId')
            except Exception:
                pass  # Timezones are nice-to-have, don't fail the whole transport

            t = Transportation.objects.create(
                user=ctx['user'], collection=ctx['collection'], name=name,
                type=transport_type, from_location=from_location,
                to_location=to_location, date=date or None,
                end_date=end_date or None, flight_number=flight_number or "",
                description=description or "",
                origin_latitude=origin_lat if origin_lat else None,
                origin_longitude=origin_lng if origin_lng else None,
                destination_latitude=dest_lat if dest_lat else None,
                destination_longitude=dest_lng if dest_lng else None,
                start_code=start_code or "",
                end_code=end_code or "",
                start_timezone=start_tz or None,
                end_timezone=end_tz or None,
                link=link or None,
                price=float(price) if price else None,
            )
            details = {'id': str(t.id), 'name': t.name}
            if start_code:
                details['from_code'] = start_code
            if end_code:
                details['to_code'] = end_code
            if start_tz:
                details['start_timezone'] = start_tz
            if end_tz:
                details['end_timezone'] = end_tz

            emoji = '✈️' if transport_type == 'plane' else '🚗' if transport_type == 'car' else '🚆' if transport_type == 'train' else '🚌' if transport_type == 'bus' else '🚗'
            code_info = f" [{start_code}→{end_code}]" if start_code and end_code else ""
            _progress(f"{emoji} Added transport: {name}{code_info}")
            return json.dumps(details)

        @tool
        def add_lodging(name: str, lodging_type: str, check_in: str, check_out: str,
                        location_name: str = "", description: str = "",
                        latitude: float = 0, longitude: float = 0,
                        english_name: str = "", reservation_number: str = "",
                        link: str = "", price: str = "") -> str:
            """Add accommodation with geocoding and timezone detection.
            Args:
                name: Hotel/lodge name as it appears in the PDF
                lodging_type: MUST be one of: hotel, hostel, resort, bnb, campground, cabin, apartment, house, villa, motel, other
                check_in: YYYY-MM-DD
                check_out: YYYY-MM-DD
                location_name: City/area (e.g. "Swakopmund, Namibia")
                description: Details (optional)
                latitude: Lat (optional, will be geocoded if not provided)
                longitude: Lng (optional, will be geocoded if not provided)
                english_name: English name for geocoding (e.g. "Strand Hotel Swakopmund") (optional)
                reservation_number: Booking/confirmation number if found in the PDF (optional)
                link: URL to the hotel's website or booking page (optional)
                price: Price as a string e.g. "250.00" in the trip's currency (optional)
            """
            valid_types = {'hotel', 'hostel', 'resort', 'bnb', 'campground', 'cabin', 'apartment', 'house', 'villa', 'motel', 'other'}
            if lodging_type not in valid_types:
                lodging_type = 'other'
            # Geocode the lodge by name if no coordinates provided
            lat, lng = latitude, longitude
            from adventures.geocoding import search as geo_search
            if (not lat or not lng):
                try:
                    # Try english_name first for better geocoding, then fall back to name
                    geocode_name = english_name or name
                    query = f"{geocode_name} {location_name}" if location_name else geocode_name
                    results = geo_search(query)
                    if isinstance(results, list) and results:
                        lat = float(results[0].get('lat', 0))
                        lng = float(results[0].get('lon', 0))
                    elif name != geocode_name:
                        # Retry with original name
                        query = f"{name} {location_name}" if location_name else name
                        results = geo_search(query)
                        if isinstance(results, list) and results:
                            lat = float(results[0].get('lat', 0))
                            lng = float(results[0].get('lon', 0))
                except Exception:
                    pass

            # Derive timezone from coordinates
            tz_name = None
            try:
                from django.conf import settings as django_settings
                import requests as req
                gmap_key = getattr(django_settings, 'GOOGLE_MAPS_API_KEY', None)
                if gmap_key and lat and lng:
                    import time as _time
                    ts = int(_time.time())
                    tz_resp = req.get(
                        f"https://maps.googleapis.com/maps/api/timezone/json?location={lat},{lng}&timestamp={ts}&key={gmap_key}",
                        timeout=5
                    )
                    if tz_resp.status_code == 200:
                        tz_data = tz_resp.json()
                        if tz_data.get('status') == 'OK':
                            tz_name = tz_data.get('timeZoneId')
            except Exception:
                pass

            l = Lodging.objects.create(
                user=ctx['user'], collection=ctx['collection'], name=name,
                type=lodging_type, check_in=check_in, check_out=check_out,
                location=location_name or "", description=description or "",
                latitude=lat if lat else None,
                longitude=lng if lng else None,
                timezone=tz_name or None,
                reservation_number=reservation_number or None,
                link=link or None,
                price=float(price) if price else None,
            )
            tz_info = f" ({tz_name})" if tz_name else ""
            _progress(f"🏨 Added lodging: {name}{tz_info}")
            return json.dumps({'id': str(l.id), 'name': l.name, 'timezone': tz_name})

        @tool
        def add_note(name: str, content: str, date: str = "", links: list = []) -> str:
            """Add a note to the trip.
            Args:
                name: Note title
                content: Markdown content
                date: YYYY-MM-DD (optional, leave empty for trip-wide notes)
                links: List of relevant URLs (e.g. visa application sites, embassy pages, tour operator links, useful travel resources) (optional)
            """
            n = Note.objects.create(
                user=ctx['user'], collection=ctx['collection'],
                name=name, content=content, date=date or None,
                links=links if links else None,
            )
            _progress(f"📝 Added note: {name}")
            return json.dumps({'id': str(n.id), 'name': n.name})

        @tool
        def add_checklist(name: str, items: list, date: str = "") -> str:
            """Add a checklist.
            Args:
                name: Checklist name
                items: List of item strings
                date: YYYY-MM-DD for day-specific checklists (optional, leave empty for trip-wide checklists like packing lists)
            """
            cl = Checklist.objects.create(
                user=ctx['user'], collection=ctx['collection'], name=name,
                date=date or None,
            )
            for item_name in items:
                ChecklistItem.objects.create(
                    user=ctx['user'], checklist=cl, name=item_name, is_checked=False,
                )
            _progress(f"✅ Added checklist: {name} ({len(items)} items)")
            return json.dumps({'id': str(cl.id), 'name': cl.name, 'items': len(items)})

        @tool
        def add_image_to_location(location_id: str, search_query: str) -> str:
            """Fetch an image for a location from multiple sources and attach it.
            Args:
                location_id: The location ID returned by add_location
                search_query: Search term in ENGLISH (e.g. 'Groot Constantia' not 'Groot Constantia Weingut')
            """
            import requests as req
            try:
                loc = Location.objects.get(id=location_id)
                headers = {'User-Agent': 'TravelArchitecture/1.0'}

                def _search_wikipedia(query):
                    """Try Wikipedia direct, then search API, then Commons."""
                    # Direct page lookup
                    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}"
                    resp = req.get(url, timeout=10, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        img = data.get('originalimage', {}).get('source') or data.get('thumbnail', {}).get('source')
                        if img:
                            return img
                    # Search API (fuzzy)
                    search_api = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json&srlimit=1"
                    resp = req.get(search_api, timeout=10, headers=headers)
                    if resp.status_code == 200:
                        results = resp.json().get('query', {}).get('search', [])
                        if results:
                            title = results[0]['title'].replace(' ', '_')
                            sr = req.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}", timeout=10, headers=headers)
                            if sr.status_code == 200:
                                img = sr.json().get('originalimage', {}).get('source') or sr.json().get('thumbnail', {}).get('source')
                                if img:
                                    return img
                    # Wikimedia Commons
                    commons = f"https://commons.wikimedia.org/w/api.php?action=query&list=search&srsearch={query}&srnamespace=6&format=json&srlimit=1"
                    resp = req.get(commons, timeout=10, headers=headers)
                    if resp.status_code == 200:
                        results = resp.json().get('query', {}).get('search', [])
                        if results:
                            ft = results[0]['title']
                            fu = f"https://commons.wikimedia.org/w/api.php?action=query&titles={ft}&prop=imageinfo&iiprop=url&format=json"
                            fr = req.get(fu, timeout=10, headers=headers)
                            if fr.status_code == 200:
                                for page in fr.json().get('query', {}).get('pages', {}).values():
                                    ii = page.get('imageinfo', [{}])
                                    if ii and ii[0].get('url'):
                                        return ii[0]['url']
                    return None

                # Try the full query first, then progressively shorter
                image_url = _search_wikipedia(search_query)
                if not image_url:
                    words = search_query.split()
                    for drop in range(1, min(len(words), 4)):
                        shorter = ' '.join(words[:-drop])
                        if len(shorter) < 3:
                            break
                        image_url = _search_wikipedia(shorter)
                        if image_url:
                            break

                # Try Google Places Photos as fallback (great for hotels/lodges)
                if not image_url:
                    from django.conf import settings as django_settings
                    gmap_key = getattr(django_settings, 'GOOGLE_MAPS_API_KEY', None)
                    if gmap_key:
                        try:
                            # Search for the place
                            places_url = "https://places.googleapis.com/v1/places:searchText"
                            places_resp = req.post(places_url, json={"textQuery": search_query, "maxResultCount": 1}, headers={
                                'Content-Type': 'application/json',
                                'X-Goog-Api-Key': gmap_key,
                                'X-Goog-FieldMask': 'places.photos',
                            }, timeout=10)
                            if places_resp.status_code == 200:
                                places = places_resp.json().get('places', [])
                                if places and places[0].get('photos'):
                                    photo_name = places[0]['photos'][0]['name']
                                    # Get the actual photo URL
                                    photo_url = f"https://places.googleapis.com/v1/{photo_name}/media?maxWidthPx=1200&key={gmap_key}"
                                    photo_resp = req.get(photo_url, timeout=10, allow_redirects=True)
                                    if photo_resp.status_code == 200 and len(photo_resp.content) > 5000:
                                        image_url = photo_url  # Store for logging
                                        # Save directly from the response content
                                        from django.core.files.base import ContentFile as CF
                                        ct = ContentType.objects.get_for_model(Location)
                                        img = ContentImage(
                                            user=ctx['user'], content_type=ct,
                                            object_id=loc.id, is_primary=True,
                                        )
                                        img.image.save(f"{loc.name[:30]}_gmap.jpg", CF(photo_resp.content), save=True)
                                        _progress(f"🖼️ Added Google Maps image for: {loc.name}")
                                        return json.dumps({'id': str(img.id), 'location': loc.name, 'source': 'google_places'})
                        except Exception:
                            pass  # Fall through to "no image found"

                if not image_url:
                    return json.dumps({'error': f'No image found for: {search_query}'})

                # Download the image
                img_resp = req.get(image_url, timeout=15, headers=headers)
                if img_resp.status_code != 200:
                    return json.dumps({'error': 'Failed to download image'})
                from django.core.files.base import ContentFile as CF
                ct = ContentType.objects.get_for_model(Location)
                img = ContentImage(
                    user=ctx['user'],
                    content_type=ct,
                    object_id=loc.id,
                    is_primary=True,
                )
                ext = image_url.split('.')[-1].split('?')[0][:4]
                img.image.save(f"{loc.name[:30]}.{ext}", CF(img_resp.content), save=True)
                _progress(f"🖼️ Added image for: {loc.name}")
                return json.dumps({'id': str(img.id), 'location': loc.name, 'image_url': image_url})
            except Exception as e:
                return json.dumps({'error': str(e)})

        @tool
        def attach_pdf_image(location_id: str, page_number: int) -> str:
            """Attach an image extracted from the uploaded PDF to a location.
            Use this when the PDF contains photos of the location (e.g. hotel photos, landscape shots).
            Args:
                location_id: The location ID returned by add_location
                page_number: The PDF page number (0-based) where the image appears
            """
            try:
                loc = Location.objects.get(id=location_id)
                # Find images from the specified page
                page_imgs = [(img_bytes, ext) for img_bytes, ext, pg in ctx['pdf_images'] if pg == page_number]
                if not page_imgs:
                    # Try nearby pages (±1)
                    page_imgs = [(img_bytes, ext) for img_bytes, ext, pg in ctx['pdf_images'] if abs(pg - page_number) <= 1]
                if not page_imgs:
                    return json.dumps({'error': f'No images found on page {page_number}'})
                # Use the largest image from that page
                img_bytes, ext = max(page_imgs, key=lambda x: len(x[0]))
                from django.core.files.base import ContentFile as CF
                ct = ContentType.objects.get_for_model(Location)
                img = ContentImage(
                    user=ctx['user'],
                    content_type=ct,
                    object_id=loc.id,
                    is_primary=not ContentImage.objects.filter(content_type=ct, object_id=loc.id).exists(),
                )
                img.image.save(f"{loc.name[:30]}_pdf.{ext}", CF(img_bytes), save=True)
                _progress(f"📷 Attached PDF image to: {loc.name} (page {page_number})")
                return json.dumps({'id': str(img.id), 'location': loc.name, 'source': 'pdf'})
            except Exception as e:
                return json.dumps({'error': str(e)})

        @tool
        def schedule_location_for_day(location_id: str, visit_date: str, order: int = 1) -> str:
            """Assign a location to a specific day in the itinerary.
            Args:
                location_id: The location ID returned by add_location
                visit_date: YYYY-MM-DD date when this location is visited
                order: Order within the day (1 = first activity, 2 = second, etc.)
            """
            try:
                loc = Location.objects.get(id=location_id)
                ct = ContentType.objects.get_for_model(Location)
                d = date.fromisoformat(visit_date)
                # Ensure the itinerary day exists
                if ctx['collection']:
                    CollectionItineraryDay.objects.get_or_create(
                        collection=ctx['collection'], date=d,
                        defaults={'name': f'Day {(d - ctx["collection"].start_date).days + 1}'}
                    )
                    item, created = CollectionItineraryItem.objects.get_or_create(
                        collection=ctx['collection'], content_type=ct, object_id=loc.id,
                        defaults={'date': d, 'order': order}
                    )
                    return json.dumps({'id': str(item.id), 'location': loc.name, 'date': visit_date, 'created': created})
                return json.dumps({'error': 'No collection created yet'})
            except Exception as e:
                return json.dumps({'error': str(e)})

        model = BedrockModel(
            model_id="eu.anthropic.claude-opus-4-5-20251101-v1:0",
            region_name=os.getenv('AWS_REGION', 'eu-west-1'),
            max_tokens=16384,
        )

        agent = Agent(
            model=model,
            tools=[create_trip, add_location, add_transportation, add_lodging, add_note, add_checklist, add_image_to_location, attach_pdf_image, schedule_location_for_day],
            system_prompt="""You are a travel itinerary parser for Travel Architecture by FaberCollins.
Given travel PDF text, you must:
1. Call create_trip with the trip name, a DETAILED description (3-5 sentences about the trip), date range, and link to the tour operator's website if found in the PDF.
2. For each destination/activity, call add_location with:
   - A descriptive name in the PDF's language (e.g. "Gorilla-Tracking im Bwindi" for German PDFs)
   - A rich description (2-3 sentences) in the PDF's language
   - Approximate lat/lng for known places
   - ALWAYS provide english_name with the SHORT, well-known ENGLISH name for geocoding.
     Use the SIMPLEST name that Google Maps would find. Drop suffixes like "Nature Reserve", "National Park" if the base name is already unique.
     Examples: "Cape of Good Hope" NOT "Cape of Good Hope Nature Reserve", "Groot Constantia" NOT "Groot Constantia Wine Estate Weingut"
   - ALWAYS provide location_display with a human-readable location string (e.g. "Swakopmund, Namibia", "Cape Town, South Africa", "Etosha, Namibia")
   - Provide tags as a list of descriptive keywords (e.g. ["safari", "wildlife"] for a game reserve, ["beach", "surfing"] for a coastal town, ["wine", "tasting"] for a vineyard)
   - Provide link to the place's official website if you know it
   - NOTE: If the user already has a location with the same name, it will be automatically linked to this trip instead of creating a duplicate.
3. For each location, FIRST try attach_pdf_image if the PDF has a photo on the same page as that location.
   Then call add_image_to_location with the SHORT ENGLISH name as search_query as a fallback.
   - Use the SHORTEST recognizable name: "Table Mountain" not "Table Mountain National Park"
   - For hotels/lodges, use the exact property name (e.g. "Chameleon Hill Forest Lodge")
4. For each location, call schedule_location_for_day to assign it to the correct day.
5. For each flight/bus/transfer, call add_transportation with COMPLETE details:
   - FLIGHTS: Always include flight_number if mentioned. Provide from_code/to_code with IATA airport codes
     (e.g. FRA for Frankfurt, JNB for Johannesburg, WDH for Windhoek, CPT for Cape Town).
     Use the full airport name for from_location/to_location (e.g. "Frankfurt Airport", "O.R. Tambo International Airport").
   - CAR/BUS: Use the city names for from_location/to_location (e.g. "Windhoek", "Swakopmund").
     The tool will geocode both endpoints to get exact coordinates for map display.
   - TRAINS: Include station names and train numbers in flight_number field.
   - Include link to the airline/operator website and price if found in the PDF.
   - Think about the FULL journey: if someone flies Frankfurt→Johannesburg→Windhoek, that's TWO separate plane legs.
     If they then rent a car Windhoek→Swakopmund, that's a separate car leg. Don't skip connecting flights or transfers.
   - The tool automatically geocodes both endpoints, derives timezones, and extracts airport codes from search results.
6. For each hotel/lodge/camp, call add_lodging with:
   - The EXACT hotel/lodge name as it appears in the PDF
   - The location_name should be the city/area (e.g. "Swakopmund, Namibia")
   - Provide english_name for better geocoding (e.g. "Strand Hotel Swakopmund")
   - Approximate lat/lng if you know the place
   - A description in the PDF's language
   - Include reservation_number if found in the PDF (booking/confirmation numbers)
   - Include link to the hotel's website and price if found in the PDF
   - The tool automatically detects the timezone from coordinates.
7. After adding each lodging, try attach_pdf_image first, then add_image_to_location with the ENGLISH property name.
8. For travel tips, requirements, or general advice, call add_note with:
   - date="" for trip-wide notes (no date = applies to whole trip)
   - Include relevant links in the links array (e.g. visa application URLs, embassy websites, travel insurance pages, tour operator contacts)
9. For packing lists, call add_checklist. Use date for day-specific checklists (e.g. "Day 1 checklist"), leave date empty for trip-wide lists (e.g. "Packing list").

IMPORTANT RULES:
- Use YYYY-MM-DD dates everywhere.
- Use real approximate coordinates for known places.
- For english_name and search_query: use the SHORTEST recognizable English name. If "Cape of Good Hope" works, don't add "Nature Reserve". If "Groot Constantia" works, don't add "Wine Estate".
- Lodging types MUST be: hotel, hostel, resort, bnb, campground, cabin, apartment, house, villa, motel, other
- Transport types MUST be: car, plane, train, bus, boat, bike, walking, other
- For flights: ALWAYS provide IATA airport codes in from_code/to_code (e.g. FRA, JNB, WDH, CPT, MUC, ZRH, LHR).
- For flights: ALWAYS include the flight_number if it appears anywhere in the PDF.
- For trains: put the train number in the flight_number field.
- Think step by step about the COMPLETE journey. A trip "Frankfurt to Namibia" likely involves:
  1. Flight Frankfurt (FRA) → Johannesburg (JNB) or Windhoek (WDH)
  2. Possibly a connecting flight JNB → WDH
  3. Car rental from Windhoek to various destinations
  4. Return flights in reverse
  Don't skip any leg — every segment of travel should be a separate add_transportation call.
- Extract ALL metadata from the PDF: prices, booking numbers, confirmation codes, website URLs, phone numbers.
- Write descriptions and location names in the SAME LANGUAGE as the PDF.
- The PDF contains {num_pdf_images} embedded images. Use attach_pdf_image to attach relevant photos from the PDF to locations.
- Be thorough — extract every detail from the PDF.
IMPORTANT: After adding each location, always try to attach an image (PDF first, then web search) and call schedule_location_for_day.""".format(num_pdf_images=len(pdf_images)),
        )

        agent(f"Parse this travel itinerary and create a complete trip:\n\n{pdf_text}")

        if ctx['collection']:
            _auto_generate_itinerary(ctx['collection'])
            note = Note.objects.create(
                user=user, collection=ctx['collection'],
                name=f"📄 Original: {pdf_filename}",
                content=f"Uploaded travel document: **{pdf_filename}**\n\nThis PDF was used by the AI agent to generate this trip itinerary.",
            )
            content_type = ContentType.objects.get_for_model(Note)
            attachment = ContentAttachment.objects.create(
                user=user, file=ContentFile(pdf_bytes, name=pdf_filename),
                name=pdf_filename, content_type=content_type, object_id=note.id,
            )
            # Update note content with download link
            note.content += f"\n\n[📎 Download {pdf_filename}](/media/{attachment.file.name})"
            note.save()

        _tasks[task_id]['status'] = 'done'
        logger.info(f"PDF import task {task_id} completed: collection {_tasks[task_id].get('collection_id')}")

    except Exception as e:
        logger.error(f"PDF import task {task_id} failed: {e}")
        _tasks[task_id]['status'] = 'error'
        _tasks[task_id]['error'] = str(e)


@method_decorator(csrf_exempt, name='dispatch')
class PdfImportView(APIView):
    """Async PDF import: POST to start, GET to poll status."""
    parser_classes = [MultiPartParser]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Upload PDF and start background processing. Returns task_id immediately."""
        user = request.user
        if 'pdf' not in request.FILES:
            return Response({'error': 'No PDF file provided. Use field name "pdf".'},
                            status=status.HTTP_400_BAD_REQUEST)

        pdf_file = request.FILES['pdf']
        if not pdf_file.name.lower().endswith('.pdf'):
            return Response({'error': 'File must be a PDF.'}, status=status.HTTP_400_BAD_REQUEST)

        pdf_bytes = pdf_file.read()
        pdf_text = _extract_pdf_text(pdf_bytes)

        if not pdf_text or len(pdf_text) < 50:
            return Response({'error': 'Could not extract text from PDF.'},
                            status=status.HTTP_400_BAD_REQUEST)

        task_id = str(uuid.uuid4())
        _tasks[task_id] = {'status': 'pending', 'collection_id': None, 'error': None, 'user_id': user.id, 'progress': []}

        thread = threading.Thread(
            target=_run_agent,
            args=(pdf_text, user, pdf_file.name, pdf_bytes, task_id),
            daemon=True,
        )
        thread.start()

        return Response({'task_id': task_id}, status=status.HTTP_202_ACCEPTED)


@method_decorator(csrf_exempt, name='dispatch')
class PdfImportStatusView(APIView):
    """Poll the status of a PDF import task."""
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        task = _tasks.get(task_id)
        if not task:
            return Response({'error': 'Task not found.'}, status=status.HTTP_404_NOT_FOUND)

        if task.get('user_id') != request.user.id:
            return Response({'error': 'Not your task.'}, status=status.HTTP_403_FORBIDDEN)

        result = {'status': task['status'], 'progress': task.get('progress', [])}

        if task['status'] == 'done' and task['collection_id']:
            result['collection_id'] = task['collection_id']
            try:
                collection = Collection.objects.get(id=task['collection_id'])
                result['collection'] = CollectionSerializer(collection, context={'request': request}).data
            except Collection.DoesNotExist:
                pass
            # Clean up completed task
            del _tasks[task_id]

        elif task['status'] == 'error':
            result['error'] = task.get('error', 'Unknown error')
            del _tasks[task_id]

        return Response(result)


@method_decorator(csrf_exempt, name='dispatch')
class PdfImportCollectionStatusView(APIView):
    """Check if a collection has an active PDF import task running."""
    permission_classes = [IsAuthenticated]

    def get(self, request, collection_id):
        """Check if there's an active import task for this collection."""
        for tid, task in _tasks.items():
            if task.get('collection_id') == str(collection_id) and task.get('user_id') == request.user.id:
                return Response({
                    'is_generating': task['status'] in ('pending', 'running'),
                    'status': task['status'],
                    'progress': task.get('progress', []),
                    'task_id': tid,
                })
        return Response({'is_generating': False, 'status': None, 'progress': [], 'task_id': None})


@method_decorator(csrf_exempt, name='dispatch')
class PdfImportRegenerateView(APIView):
    """Re-run the AI agent on a collection's stored PDF attachment."""
    parser_classes = [MultiPartParser]
    permission_classes = [IsAuthenticated]

    def post(self, request, collection_id):
        """Find the stored PDF in the collection's notes and re-run the agent."""
        user = request.user

        try:
            collection = Collection.objects.get(id=collection_id, user=user)
        except Collection.DoesNotExist:
            return Response({'error': 'Collection not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Check if there's already an active task for this collection
        for tid, task in _tasks.items():
            if task.get('collection_id') == str(collection_id) and task['status'] in ('pending', 'running'):
                return Response({'error': 'Import already in progress.', 'task_id': tid},
                                status=status.HTTP_409_CONFLICT)

        # Find the PDF attachment in the collection's notes
        pdf_attachment = None
        for note in Note.objects.filter(collection=collection):
            for att in ContentAttachment.objects.filter(
                content_type=ContentType.objects.get_for_model(Note),
                object_id=note.id
            ):
                if att.name and att.name.lower().endswith('.pdf'):
                    pdf_attachment = att
                    break
            if pdf_attachment:
                break

        if not pdf_attachment:
            return Response({'error': 'No PDF found in this collection. Upload a new PDF instead.'},
                            status=status.HTTP_404_NOT_FOUND)

        # Read the PDF bytes
        try:
            pdf_bytes = pdf_attachment.file.read()
            pdf_filename = pdf_attachment.name
        except Exception as e:
            return Response({'error': f'Could not read PDF: {str(e)}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        pdf_text = _extract_pdf_text(pdf_bytes)
        if not pdf_text or len(pdf_text) < 50:
            return Response({'error': 'Could not extract text from stored PDF.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Delete existing items from the collection (locations are M2M so just unlink them)
        collection.locations.clear()
        Transportation.objects.filter(collection=collection).delete()
        Lodging.objects.filter(collection=collection).delete()
        # Keep notes and checklists that aren't the PDF source note
        Note.objects.filter(collection=collection).exclude(
            id__in=Note.objects.filter(
                collection=collection,
                attachments__name__iendswith='.pdf'
            ).values_list('id', flat=True)
        ).delete()
        Checklist.objects.filter(collection=collection).delete()
        CollectionItineraryItem.objects.filter(collection=collection).delete()
        CollectionItineraryDay.objects.filter(collection=collection).delete()

        # Start the agent
        task_id = str(uuid.uuid4())
        _tasks[task_id] = {
            'status': 'pending', 'collection_id': str(collection.id),
            'error': None, 'user_id': user.id, 'progress': ['♻️ Regenerating from stored PDF...']
        }

        thread = threading.Thread(
            target=_run_agent,
            args=(pdf_text, user, pdf_filename, pdf_bytes, task_id),
            daemon=True,
        )
        thread.start()

        return Response({'task_id': task_id, 'collection_id': str(collection.id)},
                        status=status.HTTP_202_ACCEPTED)
