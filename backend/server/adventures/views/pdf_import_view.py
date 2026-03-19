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

    ctx = {'user': user, 'collection': None}

    try:
        _tasks[task_id]['status'] = 'running'

        def _progress(msg):
            _tasks[task_id]['progress'].append(msg)

        @tool
        def create_trip(name: str, description: str, start_date: str, end_date: str) -> str:
            """Create a new trip collection.
            Args:
                name: Trip name
                description: Brief description
                start_date: YYYY-MM-DD
                end_date: YYYY-MM-DD
            """
            collection = Collection.objects.create(
                user=ctx['user'], name=name, description=description,
                start_date=date.fromisoformat(start_date),
                end_date=date.fromisoformat(end_date), is_public=False,
            )
            ctx['collection'] = collection
            _tasks[task_id]['collection_id'] = str(collection.id)
            _progress(f"✈️ Created trip: {name}")
            return json.dumps({'id': str(collection.id), 'name': collection.name})

        @tool
        def add_location(name: str, description: str, latitude: float, longitude: float,
                         category: str = "general", category_icon: str = "🌍") -> str:
            """Add a destination to the trip.
            Args:
                name: Place name
                description: What happens here (2-3 sentences)
                latitude: Lat coordinate
                longitude: Lng coordinate
                category: Category name like: national_park, city, lake, lodge, restaurant, airport, wetland, viewpoint, wildlife, cultural, general
                category_icon: Emoji icon for the category (e.g. 🏞️ for parks, 🏙️ for cities, 🌊 for lakes, 🍽️ for restaurants, ✈️ for airports, 🦁 for wildlife, 🏛️ for cultural)
            """
            # Get or create the category for this user
            cat, _ = Category.objects.get_or_create(
                user=ctx['user'], name=category.lower().strip(),
                defaults={'display_name': category.replace('_', ' ').title(), 'icon': category_icon}
            )
            # Geocode the location by name for accurate coordinates
            lat, lng = latitude, longitude
            from adventures.geocoding import search as geo_search
            try:
                results = geo_search(name)
                if isinstance(results, list) and results:
                    lat = float(results[0].get('lat', latitude))
                    lng = float(results[0].get('lon', longitude))
            except Exception:
                pass  # Fall back to AI-provided coordinates
            loc = Location(user=ctx['user'], name=name, description=description,
                           latitude=lat, longitude=lng, category=cat)
            loc.save(_skip_geocode=False)
            if ctx['collection']:
                loc.collections.add(ctx['collection'])
            _progress(f"📍 Added location: {name} [{category}]")
            return json.dumps({'id': str(loc.id), 'name': loc.name})

        @tool
        def add_transportation(name: str, transport_type: str, from_location: str,
                               to_location: str, date: str, end_date: str = "",
                               flight_number: str = "", description: str = "") -> str:
            """Add a transport leg (flight, bus, car, etc.).
            Args:
                name: Transport name
                transport_type: car, plane, train, bus, boat, bike, walking, other
                from_location: Origin
                to_location: Destination
                date: Departure YYYY-MM-DD
                end_date: Arrival YYYY-MM-DD (optional)
                flight_number: Flight number (optional)
                description: Details (optional)
            """
            valid_types = {'car', 'plane', 'train', 'bus', 'boat', 'bike', 'walking', 'other'}
            if transport_type not in valid_types:
                transport_type = 'other'
            t = Transportation.objects.create(
                user=ctx['user'], collection=ctx['collection'], name=name,
                type=transport_type, from_location=from_location,
                to_location=to_location, date=date or None,
                end_date=end_date or None, flight_number=flight_number or "",
                description=description or "",
            )
            _progress(f"🚗 Added transport: {name}")
            return json.dumps({'id': str(t.id), 'name': t.name})

        @tool
        def add_lodging(name: str, lodging_type: str, check_in: str, check_out: str,
                        location_name: str = "", description: str = "",
                        latitude: float = 0, longitude: float = 0) -> str:
            """Add accommodation.
            Args:
                name: Hotel/lodge name
                lodging_type: MUST be one of: hotel, hostel, resort, bnb, campground, cabin, apartment, house, villa, motel, other
                check_in: YYYY-MM-DD
                check_out: YYYY-MM-DD
                location_name: City/area (optional)
                description: Details (optional)
                latitude: Lat (optional)
                longitude: Lng (optional)
            """
            valid_types = {'hotel', 'hostel', 'resort', 'bnb', 'campground', 'cabin', 'apartment', 'house', 'villa', 'motel', 'other'}
            if lodging_type not in valid_types:
                lodging_type = 'other'
            # Geocode the lodge by name if no coordinates provided
            lat, lng = latitude, longitude
            if (not lat or not lng) and name:
                from adventures.geocoding import search as geo_search
                try:
                    query = f"{name} {location_name}" if location_name else name
                    results = geo_search(query)
                    if isinstance(results, list) and results:
                        lat = float(results[0].get('lat', 0))
                        lng = float(results[0].get('lon', 0))
                except Exception:
                    pass
            l = Lodging.objects.create(
                user=ctx['user'], collection=ctx['collection'], name=name,
                type=lodging_type, check_in=check_in, check_out=check_out,
                location=location_name or "", description=description or "",
                latitude=lat if lat else None,
                longitude=lng if lng else None,
            )
            _progress(f"🏨 Added lodging: {name}")
            return json.dumps({'id': str(l.id), 'name': l.name})

        @tool
        def add_note(name: str, content: str, date: str = "") -> str:
            """Add a note to the trip.
            Args:
                name: Note title
                content: Markdown content
                date: YYYY-MM-DD (optional)
            """
            n = Note.objects.create(
                user=ctx['user'], collection=ctx['collection'],
                name=name, content=content, date=date or None,
            )
            _progress(f"📝 Added note: {name}")
            return json.dumps({'id': str(n.id), 'name': n.name})

        @tool
        def add_checklist(name: str, items: list) -> str:
            """Add a checklist.
            Args:
                name: Checklist name
                items: List of item strings
            """
            cl = Checklist.objects.create(
                user=ctx['user'], collection=ctx['collection'], name=name,
            )
            for item_name in items:
                ChecklistItem.objects.create(
                    user=ctx['user'], checklist=cl, name=item_name, is_checked=False,
                )
            _progress(f"✅ Added checklist: {name} ({len(items)} items)")
            return json.dumps({'id': str(cl.id), 'name': cl.name, 'items': len(items)})

        @tool
        def add_image_to_location(location_id: str, search_query: str) -> str:
            """Fetch a Wikipedia image for a location and attach it.
            Args:
                location_id: The location ID returned by add_location
                search_query: Search term for Wikipedia (e.g. 'Bwindi Impenetrable National Park')
            """
            import requests as req
            try:
                loc = Location.objects.get(id=location_id)
                # Search Wikipedia for the page
                search_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{search_query.replace(' ', '_')}"
                resp = req.get(search_url, timeout=10, headers={'User-Agent': 'AdventureLog/1.0'})
                if resp.status_code != 200:
                    return json.dumps({'error': 'Wikipedia page not found'})
                data = resp.json()
                image_url = data.get('originalimage', {}).get('source') or data.get('thumbnail', {}).get('source')
                if not image_url:
                    return json.dumps({'error': 'No image found on Wikipedia'})
                # Download the image
                img_resp = req.get(image_url, timeout=10, headers={'User-Agent': 'AdventureLog/1.0'})
                if img_resp.status_code != 200:
                    return json.dumps({'error': 'Failed to download image'})
                # Save as ContentImage
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
                return json.dumps({'id': str(img.id), 'location': loc.name, 'image_url': image_url})
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
            model_id="eu.anthropic.claude-sonnet-4-20250514-v1:0",
            region_name=os.getenv('AWS_REGION', 'eu-west-1'),
            max_tokens=16384,
        )

        agent = Agent(
            model=model,
            tools=[create_trip, add_location, add_transportation, add_lodging, add_note, add_checklist, add_image_to_location, schedule_location_for_day],
            system_prompt="""You are a travel itinerary parser for AdventureLog.
Given travel PDF text, you must:
1. Call create_trip with the trip name, a DETAILED description (3-5 sentences about the trip), and date range.
2. For each destination/activity, call add_location with:
   - A descriptive name (e.g. "Gorilla Tracking at Bwindi" not just "Bwindi")
   - A rich description (2-3 sentences about what happens there)
   - Approximate lat/lng for known places
3. For each location, call add_image_to_location to fetch a Wikipedia image.
4. For each location, call schedule_location_for_day to assign it to the correct day.
5. For each flight/bus/transfer, call add_transportation with the correct type.
6. For each hotel/lodge/camp, call add_lodging with:
   - The EXACT hotel/lodge name as it appears in the PDF (e.g. "Chameleon Hill Forest Lodge")
   - The location_name should be the city/area (e.g. "Lake Mutanda, Uganda")
   - Approximate lat/lng if you know the place
   - A description of the accommodation
7. After adding each lodging, call add_image_to_location with the lodging's location name to find an image.
8. For travel tips, requirements, or general advice, call add_note with date="" (no date = trip-wide context).
9. For packing lists, call add_checklist.

IMPORTANT RULES:
- Use YYYY-MM-DD dates everywhere.
- Use real approximate coordinates for known places.
- Lodging types MUST be: hotel, hostel, resort, bnb, campground, cabin, apartment, house, villa, motel, other
- Transport types MUST be: car, plane, train, bus, boat, bike, walking, other
- Write descriptions in the SAME LANGUAGE as the PDF. If the PDF is in German, write German descriptions.
- Be thorough — extract every detail from the PDF.
IMPORTANT: After adding each location, always call add_image_to_location and schedule_location_for_day for it.""",
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
