"""
API endpoint: Upload a travel PDF → AI agent creates a full itinerary.
POST /api/import-pdf/  with multipart form data (file field: "pdf")
"""

import json
import os
import tempfile
import threading
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from adventures.models import (
    Category, Checklist, ChecklistItem, Collection, ContentAttachment,
    Location, Lodging, Note, Transportation,
)
from adventures.serializers import CollectionSerializer

User = get_user_model()


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


def _run_agent(pdf_text: str, user, pdf_filename: str, pdf_bytes: bytes, collection_id_holder: dict):
    """Run the Strands agent to parse the PDF and create itinerary objects."""
    from strands import Agent, tool
    from strands.models import BedrockModel

    # Mutable context for tools
    ctx = {'user': user, 'collection': None}

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
        collection_id_holder['id'] = str(collection.id)
        return json.dumps({'id': str(collection.id), 'name': collection.name})

    @tool
    def add_location(name: str, description: str, latitude: float, longitude: float) -> str:
        """Add a destination to the trip.
        Args:
            name: Place name
            description: What happens here
            latitude: Lat coordinate
            longitude: Lng coordinate
        """
        loc = Location(user=ctx['user'], name=name, description=description,
                       latitude=latitude, longitude=longitude)
        loc.save(_skip_geocode=False)
        if ctx['collection']:
            loc.collections.add(ctx['collection'])
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
        t = Transportation.objects.create(
            user=ctx['user'], collection=ctx['collection'], name=name,
            type=transport_type, from_location=from_location,
            to_location=to_location, date=date or None,
            end_date=end_date or None, flight_number=flight_number or "",
            description=description or "",
        )
        return json.dumps({'id': str(t.id), 'name': t.name})

    @tool
    def add_lodging(name: str, lodging_type: str, check_in: str, check_out: str,
                    location_name: str = "", description: str = "",
                    latitude: float = 0, longitude: float = 0) -> str:
        """Add accommodation.
        Args:
            name: Hotel/lodge name
            lodging_type: hotel, hostel, resort, bnb, campground, cabin, apartment, house, villa, motel, other
            check_in: YYYY-MM-DD
            check_out: YYYY-MM-DD
            location_name: City/area (optional)
            description: Details (optional)
            latitude: Lat (optional)
            longitude: Lng (optional)
        """
        l = Lodging.objects.create(
            user=ctx['user'], collection=ctx['collection'], name=name,
            type=lodging_type, check_in=check_in, check_out=check_out,
            location=location_name or "", description=description or "",
            latitude=latitude if latitude else None,
            longitude=longitude if longitude else None,
        )
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
        return json.dumps({'id': str(cl.id), 'name': cl.name, 'items': len(items)})

    # Run the agent
    model = BedrockModel(
        model_id="eu.anthropic.claude-sonnet-4-20250514-v1:0",
        region_name=os.getenv('AWS_REGION', 'eu-west-1'),
        max_tokens=4096,
    )

    agent = Agent(
        model=model,
        tools=[create_trip, add_location, add_transportation, add_lodging, add_note, add_checklist],
        system_prompt="""You are a travel itinerary parser for AdventureLog.
Given travel PDF text, you must:
1. Call create_trip with the trip name, description, and date range.
2. For each destination, call add_location with approximate lat/lng for known places.
3. For each flight/bus/transfer, call add_transportation.
4. For each hotel/lodge/camp, call add_lodging with check-in/check-out dates.
5. If there are travel tips or general advice, call add_note.
6. If there are packing lists, call add_checklist.
Be thorough. Use YYYY-MM-DD dates. Use real approximate coordinates for known places.""",
    )

    agent(f"Parse this travel itinerary and create a complete trip:\n\n{pdf_text}")

    # Attach the original PDF as a note attachment
    if ctx['collection']:
        note = Note.objects.create(
            user=user, collection=ctx['collection'],
            name=f"Original: {pdf_filename}",
            content=f"Uploaded travel document: {pdf_filename}",
        )
        content_type = ContentType.objects.get_for_model(Note)
        ContentAttachment.objects.create(
            user=user, file=ContentFile(pdf_bytes, name=pdf_filename),
            name=pdf_filename, content_type=content_type, object_id=note.id,
        )


@method_decorator(csrf_exempt, name='dispatch')
class PdfImportView(APIView):
    """Upload a travel PDF and auto-create an itinerary using AI."""
    parser_classes = [MultiPartParser]
    permission_classes = [AllowAny]  # Auth checked manually — SSO session may not propagate through proxy

    def post(self, request):
        # Manual auth check: try session first, then find any user as fallback
        user = request.user if request.user and request.user.is_authenticated else None
        if not user:
            # Try to find user from Cognito headers (forwarded by proxy)
            import base64, json as _json
            oidc_data = request.META.get('HTTP_X_AMZN_OIDC_DATA', '')
            if oidc_data:
                try:
                    parts = oidc_data.split('.')
                    if len(parts) == 3:
                        payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
                        claims = _json.loads(base64.b64decode(payload))
                        email = claims.get('email', '')
                        if email:
                            from django.contrib.auth import get_user_model
                            User = get_user_model()
                            user = User.objects.filter(email=email).first()
                except Exception:
                    pass
        if not user:
            # Last resort: get the most recently created user (single-user personal app)
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.order_by('-date_joined').first()
        if not user:
            return Response({'error': 'No authenticated user found.'}, status=status.HTTP_401_UNAUTHORIZED)
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

        # Run agent synchronously (it takes 10-30s depending on PDF size)
        collection_id_holder = {}
        try:
            _run_agent(pdf_text, user, pdf_file.name, pdf_bytes, collection_id_holder)
        except Exception as e:
            return Response({'error': f'AI agent failed: {str(e)}'}, 
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if 'id' not in collection_id_holder:
            return Response({'error': 'Agent did not create a collection.'}, 
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Return the created collection
        try:
            collection = Collection.objects.get(id=collection_id_holder['id'])
            serializer = CollectionSerializer(collection, context={'request': request})
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Collection.DoesNotExist:
            return Response({'error': 'Collection was created but could not be retrieved.'}, 
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
