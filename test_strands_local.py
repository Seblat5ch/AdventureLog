"""
Local test for the Strands AI travel PDF parser.
No Django needed — just prints what the agent would create.

Usage: python test_strands_local.py "C:\path\to\your.pdf"
"""
import json
import sys

# 1. Extract text from PDF
def extract_pdf_text(path: str) -> str:
    import fitz
    doc = fitz.open(path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

# 2. Define mock tools that just print instead of writing to DB
from strands import Agent, tool
from strands.models import BedrockModel

created_items = []

@tool
def create_trip(name: str, description: str, start_date: str, end_date: str) -> str:
    """Create a new trip collection.
    Args:
        name: Trip name
        description: Brief description
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
    """
    item = {'type': 'TRIP', 'name': name, 'description': description, 'start_date': start_date, 'end_date': end_date}
    created_items.append(item)
    print(f"\n✅ TRIP: {name} ({start_date} → {end_date})")
    print(f"   {description[:100]}")
    return json.dumps({'id': 'trip-1', 'name': name})

@tool
def add_location(name: str, description: str, latitude: float, longitude: float) -> str:
    """Add a destination to the trip.
    Args:
        name: Place name
        description: What happens here
        latitude: Lat coordinate
        longitude: Lng coordinate
    """
    item = {'type': 'LOCATION', 'name': name, 'lat': latitude, 'lng': longitude}
    created_items.append(item)
    print(f"📍 LOCATION: {name} ({latitude}, {longitude}) — {description[:80]}")
    return json.dumps({'id': f'loc-{len(created_items)}', 'name': name})

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
    item = {'type': 'TRANSPORT', 'name': name, 'mode': transport_type, 'from': from_location, 'to': to_location}
    created_items.append(item)
    flight = f" [{flight_number}]" if flight_number else ""
    print(f"✈️  TRANSPORT: {name}{flight} — {from_location} → {to_location} ({date})")
    return json.dumps({'id': f'transport-{len(created_items)}', 'name': name})

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
    item = {'type': 'LODGING', 'name': name, 'check_in': check_in, 'check_out': check_out}
    created_items.append(item)
    print(f"🏨 LODGING: {name} ({lodging_type}) — {check_in} → {check_out} @ {location_name}")
    return json.dumps({'id': f'lodge-{len(created_items)}', 'name': name})

@tool
def add_note(name: str, content: str, date: str = "") -> str:
    """Add a note to the trip.
    Args:
        name: Note title
        content: Markdown content
        date: YYYY-MM-DD (optional)
    """
    item = {'type': 'NOTE', 'name': name}
    created_items.append(item)
    print(f"📝 NOTE: {name} — {content[:80]}...")
    return json.dumps({'id': f'note-{len(created_items)}', 'name': name})

@tool
def add_checklist(name: str, items: list) -> str:
    """Add a checklist.
    Args:
        name: Checklist name
        items: List of item strings
    """
    item = {'type': 'CHECKLIST', 'name': name, 'items': items}
    created_items.append(item)
    print(f"☑️  CHECKLIST: {name} ({len(items)} items)")
    for i in items[:5]:
        print(f"    - {i}")
    if len(items) > 5:
        print(f"    ... and {len(items)-5} more")
    return json.dumps({'id': f'check-{len(created_items)}', 'name': name, 'items': len(items)})


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python test_strands_local.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    print(f"📄 Extracting text from: {pdf_path}")
    text = extract_pdf_text(pdf_path)
    print(f"   Extracted {len(text)} characters\n")

    print("🤖 Starting Strands agent with Bedrock Claude...\n")
    model = BedrockModel(
        model_id="eu.anthropic.claude-sonnet-4-20250514-v1:0",
        region_name="eu-west-1",
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

    agent(f"Parse this travel itinerary and create a complete trip:\n\n{text}")

    print(f"\n{'='*60}")
    print(f"Total items created: {len(created_items)}")
    for item in created_items:
        print(f"  {item['type']}: {item['name']}")
