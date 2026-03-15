---
inclusion: manual
---

# PDF Import Agent — Remaining Improvements

## Medium Features
1. **Lodging geocoding** — Use the lodge name + location to search Google Maps / Nominatim for exact coordinates, not just approximate ones.
2. **Image search for non-locations** — Search Unsplash/Wikimedia for contextual images like "dhow boat ride", "safari Land Cruiser Uganda", "gorilla tracking Bwindi".
3. **Photo EXIF matching** — Bulk upload photos, extract GPS + timestamp from EXIF, auto-match to nearest location in the collection.
4. **Context-aware images** — If PDF mentions "7-seater Land Cruiser", search for that specifically.
5. **Multi-language support** — Agent creates notes in both the original language and English translation, or creates parallel collections per language.
6. **Original PDF viewer** — Make the attached PDF viewable in-browser (not just downloadable) for authenticated users.

## Larger Features
7. **Resume from interruption** — Store task state in DB instead of in-memory so imports survive container restarts/deployments.
8. **metadata.json import** — Support importing the same JSON format that AdventureLog's export produces.
9. **Progress streaming** — WebSocket or SSE to show real-time tool execution progress instead of polling.
10. **Images for lodgings/transport** — Fetch images for hotels and transport types (boats, safari vehicles) not just locations.
