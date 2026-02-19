# AdventureLog AWS Infrastructure

CDK project that deploys AdventureLog to AWS ECS Fargate in `eu-west-1` (Ireland) with a full CI/CD pipeline via CodeCommit + CodeBuild.

## Architecture

```
                    travel.tesem.dog
                          │
                     Route 53 (A record)
                          │
                    ACM Certificate (HTTPS)
                          │
                    ALB (path-based routing)
                    ┌─────┴──────┐
                    │            │
          /api/* /auth/*    everything else
          /admin/* /media/*       │
          /static/* /accounts/*   │
                    │            │
              Backend Fargate   Frontend Fargate
              (1 vCPU, 2GB)    (0.5 vCPU, 1GB)
              nginx + gunicorn  SvelteKit node
                    │            │
                    ├── RDS PostgreSQL 16 (PostGIS, t4g.micro)
                    ├── EFS (media uploads)
                    └── Cloud Map (service discovery: server.prod-adventurelog.local)
```

**CI/CD Pipeline:**
```
CodeCommit (git push) → CodeBuild (Docker build) → ECR (push images) → ECS (rolling deploy)
```

## Resources Created

| Resource | Details |
|----------|---------|
| VPC | 2 AZs, public + private subnets, 1 NAT gateway |
| ALB | Internet-facing, path-based routing, HTTPS via ACM |
| ACM Certificate | `travel.tesem.dog`, DNS-validated via Route 53 |
| Route 53 | A record pointing `travel.tesem.dog` → ALB |
| ECS Fargate | 2 services (backend + frontend), private subnets |
| RDS PostgreSQL 16 | PostGIS enabled, t4g.micro, private subnet, encrypted |
| EFS | Persistent media storage, mounted to backend |
| ECR | 2 repos (backend + frontend), image scan on push |
| Secrets Manager | DB credentials (auto-generated) + Django SECRET_KEY |
| Cloud Map | Private DNS namespace for frontend → backend discovery |
| CodeCommit | Source repository |
| CodeBuild | Docker builds (privileged, STANDARD_7_0) |
| CodePipeline V2 | Source → Build → Deploy (backend + frontend in parallel) |

## Prerequisites

1. AWS CLI configured with credentials
2. Node.js 22+ and npm
3. AWS CDK CLI: `npm install -g aws-cdk`
4. CDK bootstrapped in eu-west-1

## Deploy

```bash
cd infra
npm install

# Bootstrap CDK in Ireland (first time only)
cdk bootstrap aws://844633438632/eu-west-1

# Deploy the stack
cdk deploy
```

The first deploy creates all infrastructure including empty ECR repos. ECS services will fail health checks until you push code — that's expected.

## Push Code to Trigger Pipeline

After `cdk deploy` completes, grab the CodeCommit clone URL from the stack outputs:

```bash
# From the AdventureLog root directory
git remote add aws <CodeCommitRepoCloneUrlHttp from output>
git push aws main
```

This triggers the pipeline: CodeBuild builds both Docker images → pushes to ECR → ECS rolling deploy picks them up.

Every subsequent `git push aws main` triggers a full build + deploy automatically.

## PostGIS Extension

After the first successful deploy, the backend entrypoint runs Django migrations which should handle PostGIS. If it fails, connect to RDS and run manually:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

You can connect via ECS Exec:
```bash
aws ecs execute-command \
  --cluster prod-adventurelog \
  --task <task-id> \
  --container backend \
  --interactive \
  --command "/bin/bash" \
  --region eu-west-1
```

## Configuration

All config is in `cdk.json` context:

| Key | Default | Description |
|-----|---------|-------------|
| `environment` | `prod` | Environment name prefix for all resources |
| `hostedZoneId` | `Z36PXJTJTC9YNC` | Route 53 hosted zone for tesem.dog |
| `domainName` | `tesem.dog` | Base domain |
| `subdomain` | `travel` | Subdomain → `travel.tesem.dog` |

Backend env vars like `PUBLIC_URL`, `FRONTEND_URL`, `CSRF_TRUSTED_ORIGINS` are automatically set to `https://travel.tesem.dog` when a hosted zone is configured.

DB credentials are pulled from Secrets Manager at runtime — no passwords in code or env files.

## Cost Estimate (eu-west-1)

| Service | Monthly |
|---------|---------|
| Fargate (1.5 vCPU, 3GB total) | ~$45 |
| NAT Gateway | ~$35 |
| RDS t4g.micro | ~$15 |
| ALB | ~$18 |
| EFS | ~$1 |
| ECR, CodeBuild, CodePipeline | ~$5 |
| **Total** | **~$120/month** |

## Useful Commands

```bash
# Diff before deploying
cdk diff

# Destroy everything (RDS and EFS are RETAIN by default)
cdk destroy

# View CloudFormation template
cdk synth

# Check ECS service status
aws ecs describe-services --cluster prod-adventurelog --services prod-adventurelog-backend prod-adventurelog-frontend --region eu-west-1

# Tail backend logs
aws logs tail /ecs/prod-adventurelog-backend --follow --region eu-west-1

# Tail frontend logs
aws logs tail /ecs/prod-adventurelog-frontend --follow --region eu-west-1
```
