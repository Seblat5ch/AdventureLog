"""
Strands Agent: Travel PDF → AdventureLog Itinerary

Usage:
    python manage.py import_travel_pdf --pdf /path/to/itinerary.pdf --user admin
    python manage.py import_travel_pdf --pdf /path/to/itinerary.pdf --user admin --attach /path/to/guide.pdf
"""

import json
import os
from datetime import date, datetime
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.contrib.contenttypes.models import ContentType

from strands import Agent, tool
from strands.models import BedrockModel

from adventures.models import (
    Collection, Location, Transportation, Lodging, Note,
    Checklist, ChecklistItem, ContentAttachment,
)

User = get_user_model()

# ---------------------------------------------------------------
# Tools the agent can call to create AdventureLog objects
# ---------------------------------------------------------------

_context = {}  # Holds the current user and collection during a run


@tool
def create_trip(name: str, description: str, start_date: str, end_date: str) -> str:
    """Create a new trip collection in AdventureLog.

    Args:
        name: Trip name, e.g. "East Africa Safari May 2025"
        description: Brief trip description
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    """
    user = _context['user']
    collection = Collection.objects.create(
        user=user,
        name=name,
        description=description,
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        is_public=False,
    )
    _context['collection'] = collection
    return json.dumps({'id': str(collection.id), 'name': collection.name})


@tool
def add_location(name: str, description: str, latitude: float, longitude: float) -> str:
    """Add a destination/location to the current trip.

    Args:
        name: Location name, e.g. "Nairobi" or "Tarangire National Park"
        description: What happens at this location
        latitude: Latitude coordinate
        longitude: Longitude coordinate
    """
    user = _context['user']
    collection = _context['collection']
    location = Location(
        user=user,
        name=name,
        description=description,
        latitude=latitude,
        longitude=longitude,
    )
    location.save(_skip_geocode=False)
    location.collections.add(collection)
    return json.dumps({'id': str(location.id), 'name': location.name})


@tool
def add_transportation(
    name: str, transport_type: str, from_location: str, to_location: str,
    date: str, end_date: str = "", flight_number: str = "",
    description: str = ""
) -> str:
    """Add a transportation leg (flight, bus, car, etc.) to the current trip.

    Args:
        name: Transport name, e.g. "Flight JNB to NBO"
        transport_type: One of: car, plane, train, bus, boat, bike, walking, other
        from_location: Origin city/place name
        to_location: Destination city/place name
        date: Departure date/time in YYYY-MM-DD or YYYY-MM-DDTHH:MM format
        end_date: Arrival date/time (optional)
        flight_number: Flight number if applicable (optional)
        description: Additional details (optional)
    """
    user = _context['user']
    collection = _context['collection']
    transport = Transportation.objects.create(
        user=user,
        collection=collection,
        name=name,
        type=transport_type,
        from_location=from_location,
        to_location=to_location,
        date=date or None,
        end_date=end_date or None,
        flight_number=flight_number or "",
        description=description or "",
    )
    return json.dumps({'id': str(transport.id), 'name': transport.name})


@tool
def add_lodging(
    name: str, lodging_type: str, check_in: str, check_out: str,
    location_name: str = "", description: str = "",
    latitude: float = 0, longitude: float = 0
) -> str:
    """Add accommodation/lodging to the current trip.

    Args:
        name: Hotel/lodge name, e.g. "Sarova Stanley Hotel"
        lodging_type: One of: hotel, hostel, resort, bnb, campground, cabin, apartment, house, villa, motel, other
        check_in: Check-in date in YYYY-MM-DD format
        check_out: Check-out date in YYYY-MM-DD format
        location_name: City or area name (optional)
        description: Room type, meal plan, etc. (optional)
        latitude: Latitude if known (optional)
        longitude: Longitude if known (optional)
    """
    user = _context['user']
    collection = _context['collection']
    lodging = Lodging.objects.create(
        user=user,
        collection=collection,
        name=name,
        type=lodging_type,
        check_in=check_in,
        check_out=check_out,
        location=location_name or "",
        description=description or "",
        latitude=latitude if latitude else None,
        longitude=longitude if longitude else None,
    )
    return json.dumps({'id': str(lodging.id), 'name': lodging.name})


@tool
def add_note(name: str, content: str, date: str = "") -> str:
    """Add a note to the current trip (travel tips, visa info, packing lists, etc.).

    Args:
        name: Note title, e.g. "Uganda Travel Tips" or "Day 3 - Game Drive Notes"
        content: Note content in markdown format
        date: Associated date in YYYY-MM-DD format (optional)
    """
    user = _context['user']
    collection = _context['collection']
    note = Note.objects.create(
        user=user,
        collection=collection,
        name=name,
        content=content,
        date=date or None,
    )
    return json.dumps({'id': str(note.id), 'name': note.name})


@tool
def add_checklist(name: str, items: list) -> str:
    """Add a packing/preparation checklist to the current trip.

    Args:
        name: Checklist name, e.g. "Safari Packing List"
        items: List of checklist item strings, e.g. ["Sunscreen", "Binoculars", "Passport"]
    """
    user = _context['user']
    collection = _context['collection']
    checklist = Checklist.objects.create(
        user=user,
        collection=collection,
        name=name,
    )
    for item_name in items:
        ChecklistItem.objects.create(
            user=user,
            checklist=checklist,
            name=item_name,
            is_checked=False,
        )
    return json.dumps({'id': str(checklist.id), 'name': checklist.name, 'item_count': len(items)})


# ---------------------------------------------------------------
# Management command
# ---------------------------------------------------------------

class Command(BaseCommand):
    help = 'Import a travel PDF into AdventureLog using a Strands AI agent'

    def add_arguments(self, parser):
        parser.add_argument('--pdf', required=True, help='Path to the itinerary PDF')
        parser.add_argument('--user', required=True, help='Username to create the itinerary for')
        parser.add_argument('--attach', nargs='*', help='Additional PDFs to attach as notes (e.g. travel guides)')
        parser.add_argument('--region', default='eu-west-1', help='AWS region for Bedrock')

    def handle(self, *args, **options):
        # Validate user
        try:
            user = User.objects.get(username=options['user'])
        except User.DoesNotExist:
            self.stderr.write(f"User '{options['user']}' not found")
            return

        # Read PDF
        pdf_path = options['pdf']
        if not os.path.exists(pdf_path):
            self.stderr.write(f"PDF not found: {pdf_path}")
            return

        # Extract text from PDF
        pdf_text = self._extract_pdf_text(pdf_path)
        if not pdf_text:
            self.stderr.write("Could not extract text from PDF")
            return

        self.stdout.write(f"Extracted {len(pdf_text)} chars from PDF")

        # Set up context
        _context['user'] = user

        # Create the Strands agent
        model = BedrockModel(
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            region_name=options['region'],
            max_tokens=4096,
        )

        agent = Agent(
            model=model,
            tools=[create_trip, add_location, add_transportation, add_lodging, add_note, add_checklist],
            system_prompt="""You are a travel itinerary parser for AdventureLog.

Given a travel PDF text, you must:
1. First call create_trip with the trip name, description, and date range.
2. For each destination mentioned, call add_location with coordinates (look up approximate lat/lng for known places).
3. For each flight, bus, or transfer, call add_transportation.
4. For each hotel/lodge/camp, call add_lodging with check-in/check-out dates.
5. If there are travel tips, visa info, or general advice, call add_note to store them.
6. If there are packing lists or preparation items, call add_checklist.

Be thorough — extract every day, every accommodation, every transport leg.
Use real approximate coordinates for well-known places (cities, national parks, etc.).
Dates should be in YYYY-MM-DD format.
For lodging types, use: hotel, resort, campground, cabin, or other as appropriate.
For transport types, use: plane, bus, car, or other as appropriate.""",
        )

        self.stdout.write("Running Strands agent to parse itinerary...")
        response = agent(f"Parse this travel itinerary and create a complete trip in AdventureLog:\n\n{pdf_text}")
        self.stdout.write(f"\nAgent response:\n{response}")

        collection = _context.get('collection')
        if not collection:
            self.stderr.write("Agent did not create a collection")
            return

        # Attach additional PDFs as note attachments
        for attach_path in (options.get('attach') or []):
            if os.path.exists(attach_path):
                self._attach_pdf(collection, user, attach_path)
                self.stdout.write(f"Attached: {attach_path}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Collection '{collection.name}' created with ID {collection.id}"
        ))

    def _extract_pdf_text(self, pdf_path: str) -> str:
        """Extract text from a PDF file."""
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text
        except ImportError:
            # Fallback: try pdfplumber
            try:
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    return "\n".join(page.extract_text() or "" for page in pdf.pages)
            except ImportError:
                self.stderr.write("Install PyMuPDF or pdfplumber: pip install PyMuPDF pdfplumber")
                return ""

    def _attach_pdf(self, collection: Collection, user, pdf_path: str):
        """Attach a PDF file to a note in the collection."""
        filename = os.path.basename(pdf_path)
        with open(pdf_path, 'rb') as f:
            content = f.read()

        # Create a note for the attachment
        note = Note.objects.create(
            user=user,
            collection=collection,
            name=f"Attached: {filename}",
            content=f"Travel document: {filename}",
        )

        # Attach the PDF
        content_type = ContentType.objects.get_for_model(Note)
        ContentAttachment.objects.create(
            user=user,
            file=ContentFile(content, name=filename),
            name=filename,
            content_type=content_type,
            object_id=note.id,
        )
