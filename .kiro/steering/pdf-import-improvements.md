---
inclusion: manual
---

# PDF Import Agent — Improvement Backlog

## Quick Fixes (do now)
1. **Lodging names/locations** — Agent should include the lodge/hotel name AND its location (city/area) so AdventureLog can geocode it. Currently only puts generic location names.
2. **Images for lodgings** — Agent only adds images to locations, not lodgings or transportations. Add image fetching for lodgings too.
3. **Packing list → Trip Context** — Checklists and general notes should be marked as `is_global=True` in the itinerary (trip-wide context) instead of being unscheduled.
4. **Longer descriptions** — Agent should write richer descriptions for locations and lodgings, not just one-liners.
5. **Original PDF link** — The PDF is already attached as a ContentAttachment on a Note. Need to verify the note is accessible and the PDF is downloadable/viewable.

## Medium Features (next session)
6. **Multi-language support** — Agent could create notes in both the original language and English translation. Or create two collections (one per language).
7. **Lodging geocoding** — Use the lodge name + location to search Google Maps / Nominatim for exact coordinates, not just approximate ones.
8. **Image search for non-locations** — Search Unsplash/Wikimedia for contextual images like "dhow boat ride", "safari Land Cruiser Uganda", "gorilla tracking Bwindi".
9. **Photo EXIF matching** — Bulk upload photos, extract GPS + timestamp from EXIF, auto-match to nearest location in the collection.
10. **Context-aware images** — If PDF mentions "7-seater Land Cruiser", search for that specifically.

## Larger Features (future)
11. **Resume from interruption** — Store task state in DB instead of in-memory so imports survive container restarts.
12. **metadata.json import** — Support importing the same JSON format that AdventureLog's export produces.
13. **Progress streaming** — WebSocket or SSE to show real-time tool execution progress instead of polling.
