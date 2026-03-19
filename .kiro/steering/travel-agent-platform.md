---
inclusion: manual
---

# AdventureLog — Product Vision & Backlog

## What's Built & Working

- ECS Fargate deployment (CloudFront → ALB + Cognito SSO → Frontend/Backend)
- Strands AI PDF import with 8 tools (trip, locations, lodging, transport, notes, checklists, Wikipedia images, itinerary scheduling)
- Async import with polling + real-time progress display
- WAF with tuned exclusions for file uploads
- CodePipeline CI/CD (git push → build → deploy)

## Next Up: Technical Improvements

### PDF Import Agent
- **Images for lodgings/transport** — fetch images for hotels, boats, safari vehicles (not just locations)
- **Context-aware images** — if PDF says "7-seater Land Cruiser", search for that
- **Trip enhancement mode** — detect matching existing collections by date/destination and enrich instead of creating duplicates (e.g. upload itinerary first, then travel story to add personal anecdotes)
- **Multi-language** — create notes in both original language and English translation
- **Original PDF viewer** — make attached PDF viewable in-browser, not just downloadable

### Infrastructure
- Store task status in DB instead of in-memory (survives container restarts)
- Set `minimumHealthyPercent: 100` on ECS to prevent killing containers mid-import
- Remove debug SSO logging from `hooks.server.ts`
- **Move media storage from EFS to S3** — use `django-storages` with S3 backend. Cheaper ($0.023/GB vs $0.30/GB), scalable, and enables CloudFront CDN for media. Needs: S3 bucket in CDK, IAM permissions on backend task role, `django-storages` package, update `STORAGES` setting, remove EFS mount.

### Frontend
- Some client-side `fetch('/api/...')` calls may still 403 in edge cases
- Consider SvelteKit `handleFetch` hook to intercept all client-side fetches
- Photo EXIF matching: bulk upload photos → extract GPS + timestamp → auto-match to nearest location

## Future: B2B Travel Agency Platform

Transform AdventureLog into a white-label platform for travel agencies.

### Phase 1: Multi-Traveller Assignment
- "Assign Travellers" UI after PDF import (list of email addresses)
- Auto-create Cognito users, share collection with all travellers
- Restrict PDF import to `is_staff` users

### Phase 2: Email Notifications
- Amazon SES for "Your trip is ready" emails with login link
- Reminder emails before trip start date

### Phase 3: White-Label Branding
- Agency profile: logo, colors, business name
- Themed login page, branded email templates
- Custom subdomain per agency

### Phase 4: Offline & Export
- PWA for offline access
- PDF export of itinerary
- Offline map tiles caching

### Phase 5: Multi-Agency SaaS
- Tenant isolation, billing (Stripe), agency dashboard, API
