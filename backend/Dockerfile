# Stage 1: Build stage with dependencies
FROM public.ecr.aws/docker/library/python:3.13-slim AS builder

# Metadata labels
LABEL maintainer="Sean Morley" \
      version="0.10.0" \
      description="AdventureLog — the ultimate self-hosted travel companion." \
      org.opencontainers.image.title="AdventureLog" \
      org.opencontainers.image.description="AdventureLog helps you plan, track, and share your adventures." \
      org.opencontainers.image.version="0.10.0" \
      org.opencontainers.image.authors="Sean Morley" \
      org.opencontainers.image.source="https://github.com/seanmorley15/AdventureLog" \
      org.opencontainers.image.vendor="Sean Morley" \
      org.opencontainers.image.licenses="GPL-3.0"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /code
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies needed for build
# Apply latest OS security patches (Shepherd VMRO:Quibbler remediation - per FAQ Q13)
# to ensure CVE fixes are picked up even before the base image is rebuilt upstream.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
      git \
      postgresql-client \
      gdal-bin \
      libgdal-dev \
      nginx \
      memcached \
      supervisor \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY ./server/requirements.txt /code/
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Stage 2: Final image with runtime dependencies
FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /code
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies (including GDAL)
# Apply latest OS security patches (Shepherd VMRO:Quibbler remediation - per FAQ Q13)
# to ensure CVE fixes for python3.13, nginx, postgresql-17, glibc, linux, systemd, etc.
# are picked up even before the base image is rebuilt upstream.
#
# NOTE: postgresql-client is intentionally NOT installed. It depends on perl, which
# currently ships unpatched CVEs (e.g. CVE-2026-42496 Archive::Tar, CRITICAL) that
# Debian trixie has no fix for yet and that page us via Shepherd. The DB-readiness
# check in entrypoint.sh uses Django/psycopg2 instead, so psql is not needed at runtime.
# We then purge the full perl interpreter + modules (NOT the Essential perl-base, which
# does not contain Archive::Tar) so the flagged package is absent from the dpkg DB that
# Inspector/Shepherd scans. gdal-bin 3.5+ dropped its perl bindings, so GDAL is unaffected.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
      gdal-bin \
      libgdal-dev \
      nginx \
      memcached \
      supervisor \
    && ( apt-get purge -y perl 'perl-modules-*' || true ) \
    && apt-get autoremove -y \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Fail the build loudly if removing perl/postgresql-client broke the runtime stack.
# This is a safety gate: a broken image never reaches ECS (the pipeline stops here),
# so the currently-healthy task keeps running instead of deploying a broken container.
RUN set -e; \
    echo "Smoke test: perl must be absent; Django + GDAL must load"; \
    if dpkg -l perl 2>/dev/null | grep -q '^ii'; then echo "FAIL: perl still installed"; exit 1; fi; \
    python3 -c "import django; print('django', django.get_version())"; \
    python3 -c "from django.contrib.gis.gdal import gdal_version; print('gdal', gdal_version())"

# Copy project code and configs
COPY ./server /code/
COPY ./nginx.conf /etc/nginx/nginx.conf
COPY ./supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY ./entrypoint.sh /code/entrypoint.sh
RUN chmod +x /code/entrypoint.sh \
    && mkdir -p /code/static /code/media

# Collect static files
RUN python3 manage.py collectstatic --noinput --verbosity 2

# Expose ports
EXPOSE 80 8000

# Start with an entrypoint that runs init tasks then starts supervisord
ENTRYPOINT ["/code/entrypoint.sh"]

# Start supervisord to manage processes
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
