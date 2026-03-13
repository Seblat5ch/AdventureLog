# AdventureLog AWS Infrastructure

CDK project that deploys AdventureLog to AWS ECS Fargate in `eu-west-1` (Ireland) with CloudFront, Cognito SSO, and CI/CD via CodeCommit + CodeBuild.

## Architecture

```
                    travel.tesem.dog
                          │
                     Route 53 (A record)
                          │
                  CloudFront Distribution
                  (us-east-1 ACM cert, HTTP→HTTPS, caching disabled)
                          │
                    ALB (HTTPS only, port 443, 120s idle timeout)
                    Cognito auth on all routes
                    WAF (CommonRuleSet*, KnownBadInputs, IpReputation)
                    ┌─────┴──────┐
                    │            │
          /media/* /static/*   everything else (including /api/*, /auth/*)
          /admin/* /accounts/*        │
                    │                 │
              Backend Fargate    Frontend Fargate (SvelteKit)
              (1 vCPU, 2GB)     (0.5 vCPU, 1GB)
              nginx + gunicorn   proxies /api/* and /auth/* to backend
                    │            via Cloud Map DNS
                    ├── RDS PostgreSQL 16 (PostGIS, t4g.micro)
                    ├── EFS (media uploads at /code/media)
                    ├── Bedrock Claude Sonnet 4 (Strands AI agent)
                    └── Cloud Map (server.prod-adventurelog.local)
```

*WAF excludes SizeRestrictions_BODY, CrossSiteScripting_BODY, NoUserAgent_HEADER to avoid false positives on file uploads and rich content.

**Key routing decision:** `/api/*` and `/auth/*` go to the frontend (SvelteKit), NOT directly to the backend. SvelteKit's server-side proxy handles CSRF tokens, session cookies, and forwards to the backend via Cloud Map. This avoids issues with browser `fetch()` calls hitting ALB Cognito auth redirects.

**Security:**
- CloudFront in front of ALB — ALB SG restricted to CloudFront prefix list only
- ALB port 80 removed — only HTTPS (443) with Cognito authenticate-cognito action
- CloudFront caching disabled (dynamic app with per-user auth)
- WAF with 3 AWS managed rule groups on ALB
- Cognito User Pool with OAuth2 authorization code grant
- Django middleware auto-creates users from Cognito OIDC headers (true SSO)
- First real SSO user auto-promoted to superuser

**CI/CD Pipeline:**
```
CodeCommit (git push) → CodeBuild (Docker build) → ECR (push images) → ECS (rolling deploy)
```

## AI Features — PDF Travel Itinerary Import

Upload a travel PDF at `/collections/import` and the Strands AI agent (Claude Sonnet 4 on Bedrock) creates a complete trip:

**How it works:**
1. Frontend uploads PDF → backend extracts text with PyMuPDF
2. Backend returns `task_id` immediately (async, no timeout risk)
3. Frontend polls for status every 3 seconds
4. Background thread runs Strands agent with 8 tools:

| Tool | What it does |
|------|-------------|
| `create_trip` | Creates the collection with dates |
| `add_location` | Adds destinations with lat/lng coordinates |
| `add_image_to_location` | Fetches high-res Wikipedia image for each location |
| `schedule_location_for_day` | Assigns location to correct itinerary day |
| `add_transportation` | Flights, drives, boats, etc. |
| `add_lodging` | Hotels, lodges with check-in/out |
| `add_note` | Travel tips, requirements, pricing |
| `add_checklist` | Packing lists |

**Example output from a 10-page Uganda safari PDF:**
- 13 locations with Wikipedia images and map coordinates
- 9 accommodations with check-in/out dates
- 11 transportation segments
- 3 detailed notes
- 1 packing checklist with 20+ items
- All items scheduled to correct itinerary days

## Resources Created

| Resource | Details |
|----------|---------|
| VPC | 2 AZs, public + private subnets, 1 NAT gateway |
| CloudFront | Distribution with us-east-1 ACM cert, HTTPS redirect, 120s origin timeout |
| ALB | Internet-facing, HTTPS only, Cognito auth, WAF, 120s idle timeout |
| ACM Certificates | eu-west-1 (ALB) + us-east-1 (CloudFront) |
| Route 53 | A record → CloudFront distribution |
| Cognito | User Pool + App Client + hosted UI domain |
| WAF | CommonRuleSet (with exclusions), KnownBadInputs, IpReputation |
| ECS Fargate | 2 services (backend + frontend), private subnets |
| RDS PostgreSQL 16 | PostGIS, t4g.micro, private subnet |
| EFS | Persistent media storage, mounted to backend |
| ECR | 2 repos (backend + frontend) |
| Secrets Manager | DB credentials + Django SECRET_KEY |
| Cloud Map | Private DNS for frontend → backend |
| CodeCommit | Source repository |
| CodeBuild | Docker builds (privileged, STANDARD_7_0) |
| CodePipeline V2 | Source → Build → Deploy |

## Deploy

```bash
cd infra
npm install
npx cdk bootstrap aws://ACCOUNT_ID/eu-west-1  # first time only
npx cdk deploy -c environment=prod -c hostedZoneId=ZONE_ID -c domainName=tesem.dog -c subdomain=travel
```

ECS services start with GHCR public images. Push code to trigger the pipeline:

```bash
git remote add aws <CodeCommitRepoCloneUrlHttp from output>
git push aws main
```

Then create a Cognito admin user:
```bash
POOL_ID=<CognitoUserPoolId from output>
aws cognito-idp admin-create-user --user-pool-id $POOL_ID \
  --username admin@tesem.dog \
  --user-attributes Name=email,Value=admin@tesem.dog Name=email_verified,Value=true \
  --temporary-password TravelAdmin2026! --message-action SUPPRESS --region eu-west-1

aws cognito-idp admin-set-user-password --user-pool-id $POOL_ID \
  --username admin@tesem.dog --password TravelAdmin2026! --permanent --region eu-west-1
```

First login via Cognito auto-creates a Django superuser.

## Configuration

`cdk.json` context:

| Key | Default | Description |
|-----|---------|-------------|
| `environment` | `prod` | Resource name prefix |
| `hostedZoneId` | | Route 53 hosted zone ID |
| `domainName` | `tesem.dog` | Base domain |
| `subdomain` | `travel` | → `travel.tesem.dog` |

## Cost Estimate (eu-west-1)

| Service | Monthly |
|---------|---------|
| Fargate (1.5 vCPU, 3GB) | ~$45 |
| NAT Gateway | ~$35 |
| ALB | ~$18 |
| RDS t4g.micro | ~$15 |
| CloudFront | ~$1 |
| EFS, ECR, CodeBuild, Pipeline | ~$6 |
| Bedrock (per import) | ~$0.05 |
| **Total** | **~$120/month** |

## Useful Commands

```bash
cdk diff                    # Preview changes
cdk deploy                  # Deploy infra
cdk destroy                 # Tear down (delete RDS/EFS/ECR manually after)
git push aws main           # Trigger CI/CD pipeline

# ECS status
aws ecs describe-services --cluster prod-adventurelog \
  --services prod-adventurelog-backend prod-adventurelog-frontend --region eu-west-1

# Tail logs
aws logs tail /ecs/prod-adventurelog-backend --follow --region eu-west-1
aws logs tail /ecs/prod-adventurelog-frontend --follow --region eu-west-1

# Shell into backend container
TASK_ID=$(aws ecs list-tasks --cluster prod-adventurelog \
  --service-name prod-adventurelog-backend --region eu-west-1 \
  --query "taskArns[0]" --output text | awk -F/ '{print $NF}')
aws ecs execute-command --cluster prod-adventurelog \
  --task $TASK_ID --container backend --interactive --command /bin/bash --region eu-west-1
```
