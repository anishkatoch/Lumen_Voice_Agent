# ─── Stage 1: Build Next.js frontend ───────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json* frontend/yarn.lock* ./
RUN npm install --legacy-peer-deps || npm install

COPY frontend/ .

# Frontend and backend are served from the same origin in this container,
# so the API base must be relative — NOT localhost (that resolves to the
# visitor's own machine, not this server).
# NEXT_PUBLIC_* values are baked into the static bundle at build time (they're
# not secrets — Supabase anon key/URL are public, protected by RLS, not by hiding them).
ARG NEXT_PUBLIC_API_URL=""
ARG NEXT_PUBLIC_SUPABASE_URL="https://uqrojyxahyytvjrbyska.supabase.co"
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVxcm9qeXhhaHl5dHZqcmJ5c2thIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY0OTQwNTEsImV4cCI6MjA5MjA3MDA1MX0.1c8ufc0YH63XM94F8bvXghr5-B0hUD06y3d3np3AEj0"
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_PUBLIC_SUPABASE_URL=${NEXT_PUBLIC_SUPABASE_URL}
ENV NEXT_PUBLIC_SUPABASE_ANON_KEY=${NEXT_PUBLIC_SUPABASE_ANON_KEY}

RUN npm run build
# output: "export" in next.config.mjs makes `next build` emit static files to ./out

# ─── Stage 2: Run FastAPI + serve frontend ──────────────────────────────
FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Force unbuffered stdout/stderr so print() logs show up immediately in
# container logs instead of sitting in a buffer (Python defaults to
# block-buffering when stdout isn't a TTY, which Docker's log capture isn't).
ENV PYTHONUNBUFFERED=1

# Copy Python dependency files
COPY pyproject.toml uv.lock ./

# Install Python dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY app/ ./app/
COPY config.yaml ./

# Copy built Next.js static export from builder stage
COPY --from=frontend-builder /frontend/out ./frontend/out

# Run FastAPI (which serves both API and frontend)
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
