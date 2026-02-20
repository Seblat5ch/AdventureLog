# AdventureLog AWS Infrastructure

CDK project that deploys AdventureLog to AWS ECS Fargate in `eu-west-1` (Ireland) with CloudFront, Cognito SSO, and CI/CD via CodeCommit + CodeBuild.

## Architecture

```
                    travel.tesem.dog
                          │
                     Route 53 (A record)
                          │
                  CloudFront Distribution
                  (us-east-1 ACM cert, HTTP→HTTPS redirect)
                          │
                    ALB (HTTPS only, port 443)
                    Cognito auth on all routes
                    WAF (CommonRuleSet, KnownBadInputs, IpReputation)
                    ┌─────┴──────┐
                    │            │
          /api/* /auth/*    everything else
          /admin/* /media/*       │
          /static/* /accounts/*   │
                    │            │
              Backend Fargate   Frontend Fargate
              (1 vCPU, 2GB)    (0.5 vCPU, 1GB)
              nginx + gunicorn  SvelteKit node
                    │
                    ├── RDS PostgreSQL 16 (PostGIS, t4g.micro)
                    ├── EFS (media uploads at /code/media)
                    ├── Bedrock Claude (Strands AI agent for PDF import)
                    └── Cloud Map (server.prod-adventurelog.local)
```

**Security:**
- CloudFront in front of ALB — DyePack scans ALB ENI IPs but not CloudFront IPs
- ALB port 80 removed — only HTTPS (443) with Cognito authenticate-cognito action
- WAF with 3 AWS managed rule groups on ALB
- Cognito User Pool with OAuth2 authorization code grant
- Django middleware auto-creates users from Cognito OIDC headers (true SSO)

**CI/CD Pipeline:**
```
CodeCommit (git push) → CodeBuild (Docker build) → ECR (push images) → ECS (rolling deploy)
```

## Resources Created

| Resource | Details |
|----------|---------|
| VPC | 2 AZs, public + private subnets, 1 NAT gateway |
| CloudFront | Distribution with us-east-1 ACM cert, HTTPS redirect |
| ALB | Internet-facing, HTTPS only, Cognito auth, WAF |
| ACM Certificates | eu-west-1 (ALB) + us-east-1 (CloudFront) |
| Route 53 | A record → CloudFront distribution |
| Cognito | User Pool + App Client + hosted UI domain |
| WAF | CommonRuleSet, KnownBadInputs, IpReputation |
| ECS Fargate | 2 services (backend + frontend), private subnets |
| RDS PostgreSQL 16 | PostGIS, t4g.micro, private subnet |
| EFS | Persistent media storage, mounted to backend |
| ECR | 2 repos (backend + frontend) |
| Secrets Manager | DB credentials + Django SECRET_KEY |
| Cloud Map | Private DNS for frontend → backend |
| CodeCommit | Source repository |
| CodeBuild | Docker builds (privileged, STANDARD_7_0) |
| CodePipeline V2 | Source → Build → Deploy |

## AI Features

**PDF Travel Itinerary Import** (`/collections/import`):
- Drag-and-drop a travel PDF
- Strands AI agent (Claude Sonnet on Bedrock) parses the document
- Auto-creates trip collection with locations, flights, hotels, notes, checklists
- Original PDF attached as a note
- Backend task role has `bedrock:InvokeModel` permissions

## Deploy

```bash
cd infra
npm install
cdk bootstrap aws://844633438632/eu-west-1  # first time only
cdk deploy
```

ECS services start with GHCR public images. Push code to trigger the pipeline:

```bash
# From AdventureLog root
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

## Promote a User to Admin

Cognito SSO auto-creates regular Django users on first login. To grant admin/staff access, exec into the backend container:

```bash
# Find the running backend task
TASK_ID=$(aws ecs list-tasks --cluster prod-adventurelog \
  --service-name prod-adventurelog-backend --region eu-west-1 \
  --query "taskArns[0]" --output text | awk -F/ '{print $NF}')

# Shell into the container
aws ecs execute-command --cluster prod-adventurelog \
  --task $TASK_ID --container backend --interactive --command /bin/bash --region eu-west-1
```

Then inside the container:
```bash
# List all users
python /code/manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); [print(u.username, u.email, u.is_staff) for u in User.objects.all()]"

# Promote a user to superuser
python /code/manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); u = User.objects.get(email='sebastian@tesem.dog'); u.is_staff = True; u.is_superuser = True; u.save(); print('Done:', u.username)"
```

## Configuration

`cdk.json` context:

| Key | Default | Description |
|-----|---------|-------------|
| `environment` | `prod` | Resource name prefix |
| `hostedZoneId` | `Z36PXJTJTC9YNC` | Route 53 hosted zone |
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
| **Total** | **~$120/month** |

## Useful Commands

```bash
cdk diff                    # Preview changes
cdk deploy                  # Deploy infra
cdk destroy                 # Tear down
git push aws main           # Trigger CI/CD pipeline

# ECS status
aws ecs describe-services --cluster prod-adventurelog \
  --services prod-adventurelog-backend prod-adventurelog-frontend --region eu-west-1

# Tail logs
aws logs tail /ecs/prod-adventurelog-backend --follow --region eu-west-1
aws logs tail /ecs/prod-adventurelog-frontend --follow --region eu-west-1

# Shell into backend container
aws ecs execute-command --cluster prod-adventurelog \
  --task <task-id> --container backend --interactive --command /bin/bash --region eu-west-1
```
