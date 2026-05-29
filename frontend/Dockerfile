# Use this image as the platform to build the app
FROM public.ecr.aws/docker/library/node:22-alpine AS external-website

# Metadata labels for the AdventureLog image
LABEL maintainer="Sean Morley" \
      version="v0.12.0" \
      description="AdventureLog — the ultimate self-hosted travel companion." \
      org.opencontainers.image.title="AdventureLog" \
      org.opencontainers.image.description="AdventureLog is a self-hosted travel companion that helps you plan, track, and share your adventures." \
      org.opencontainers.image.version="v0.12.0" \
      org.opencontainers.image.authors="Sean Morley" \
      org.opencontainers.image.url="https://raw.githubusercontent.com/seanmorley15/AdventureLog/refs/heads/main/brand/banner.png" \
      org.opencontainers.image.source="https://github.com/seanmorley15/AdventureLog" \
      org.opencontainers.image.vendor="Sean Morley" \
      org.opencontainers.image.created="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
      org.opencontainers.image.licenses="GPL-3.0"

# The WORKDIR instruction sets the working directory for everything that will happen next
WORKDIR /app

# Install pnpm globally first.
# Pin to v10: pnpm v11 dropped reading the `pnpm` field from package.json, so it
# ignores pnpm.overrides and mismatches the committed lockfile (overrides), which
# breaks `pnpm install --frozen-lockfile` (ERR_PNPM_LOCKFILE_CONFIG_MISMATCH).
RUN npm install -g pnpm@10.34.1

# Copy package files first for better Docker layer caching
COPY package.json pnpm-lock.yaml* ./

# Clean install all node modules using pnpm with frozen lockfile
RUN pnpm install --frozen-lockfile

# Copy the rest of the application files
COPY . .

# Remove the development .env file if present
RUN rm -f .env

# Build SvelteKit app
RUN pnpm run build

# Make startup script executable
RUN chmod +x ./startup.sh

# Change to non-root user for security
USER node:node

# Expose the port that the app is listening on
EXPOSE 3000

# Run startup.sh instead of the default command
CMD ["./startup.sh"]