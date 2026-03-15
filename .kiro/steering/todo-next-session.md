---
inclusion: manual
---

# Next Session TODO

## Priority 1: Photo Auto-Matching by EXIF Metadata
User bulk-uploads photos to a collection → backend reads EXIF (GPS coords, timestamp) → matches each photo to the nearest location by coordinates and date → auto-attaches to that location.

- Use `pillow` (already installed) for EXIF extraction
- Match by: GPS distance to location lat/lng + date overlap with visit dates
- New endpoint: `POST /api/collections/{id}/auto-assign-photos/`
- Frontend: drag-and-drop zone on the collection page for bulk photo upload

## Priority 2: Frontend Client-Side Fetch
- Some client-side `fetch('/api/...')` calls from component `<script>` blocks still go directly through CloudFront → ALB instead of SvelteKit proxy. WAF exclusions handle most cases but edge cases may 403.
- Consider adding a SvelteKit `handleFetch` hook to intercept all client-side fetches.
- Remove debug SSO logging from `hooks.server.ts`.

## Priority 3: Infrastructure Hardening
- Store PDF import task status in database instead of in-memory (survives container restarts during deployments).
- Set `minimumHealthyPercent: 100` on ECS services to prevent killing old containers while agent is running.

## See Also
- `.kiro/steering/pdf-import-improvements.md` for Strands agent feature backlog (images for lodging, multi-language, contextual image search, etc.)
