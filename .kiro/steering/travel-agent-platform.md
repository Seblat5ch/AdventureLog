---
inclusion: manual
---

# Travel Agent Platform — Product Vision & Implementation Plan

## Concept

Transform AdventureLog from a personal travel companion into a white-label B2B platform for travel agencies. A travel agent (e.g. Bettina Collins) drops a trip PDF, the AI creates a full itinerary, and all travellers in the party get access to a branded interactive trip page with map, timeline, hotels, flights, and packing lists.

## User Personas

- **Agency Admin** (Bettina): Manages trips, imports PDFs, assigns travellers, customizes branding
- **Traveller** (customer): Views their trip, gets email notifications, can use the app offline

## Core Flow

```
1. Admin drops PDF → Strands AI creates itinerary (DONE - working today)
2. Admin assigns travellers by email → system creates accounts + shares collection
3. Travellers get email → "Your trip to Uganda is ready" with login link
4. Travellers log in → see branded trip page with map, timeline, details
5. Travellers can install as PWA or export PDF for offline use
```

## Features to Build

### Phase 1: Multi-Traveller Assignment
- Add "Assign Travellers" UI after PDF import (list of email addresses)
- Auto-create Cognito users for each traveller email
- Share the collection with all travellers (use existing `shared_with` field on Collection model)
- Admin-only PDF import: restrict `/api/import-pdf/` to `is_staff` users
- Files: `backend/server/adventures/views/pdf_import_view.py`, `frontend/src/routes/collections/import/+page.svelte`

### Phase 2: Email Notifications
- Integrate Amazon SES for transactional emails
- Send "Your trip is ready" email with login link when a trip is assigned
- Send reminder emails before trip start date
- CDK: add SES identity, IAM permissions to backend task role
- Files: new `backend/server/adventures/notifications.py`, `infra/lib/constructs/fargate-construct.ts`

### Phase 3: White-Label Branding
- Agency profile model: logo, colors, business name, custom domain
- Themed login page (Cognito hosted UI customization or custom UI)
- Branded email templates with agency logo
- Custom subdomain per agency (e.g. `bettina.travel.tesem.dog`)
- Files: new `backend/server/adventures/models.py` (AgencyProfile), frontend theme system

### Phase 4: Offline & Export
- PWA manifest + service worker for offline access (SvelteKit has built-in support)
- PDF export of itinerary (use WeasyPrint or similar on backend)
- "Add to Home Screen" prompt on mobile
- Offline map tiles caching for trip destinations
- Files: `frontend/static/manifest.json`, `frontend/src/service-worker.ts`

### Phase 5: Multi-Agency SaaS (Optional)
- Tenant isolation (each agency is a separate "organization")
- Billing integration (Stripe) — per-trip or monthly subscription
- Agency dashboard with trip analytics
- API for integration with existing booking systems

## Technical Notes

### Existing Infrastructure (already deployed)
- ECS Fargate (backend + frontend) in eu-west-1
- CloudFront → ALB with Cognito SSO
- RDS PostgreSQL with PostGIS
- EFS for media storage
- CodePipeline CI/CD (git push → build → deploy)
- Strands AI agent with Bedrock Claude (eu. cross-region inference profile)
- WAF with managed rule groups

### Key Models (already exist in `backend/server/adventures/models.py`)
- `Collection` — has `shared_with` ManyToMany field, `is_public` flag
- `Location` — lat/lng, linked to collections
- `Transportation` — from/to, dates, flight numbers
- `Lodging` — check-in/out, type
- `Note`, `Checklist`, `ChecklistItem`

### Cognito User Management
- User pool: managed by CDK in `infra/lib/constructs/alb-construct.ts`
- Admin creates users via CLI or future admin UI
- SSO middleware: `backend/server/adventures/middleware_cognito.py`
- Auto-creates Django user on first Cognito login

### Bedrock Model Config
- Model: `eu.anthropic.claude-sonnet-4-20250514-v1:0` (EU cross-region inference profile)
- IAM: backend task role has `bedrock:InvokeModel` permission
- Region: eu-west-1
