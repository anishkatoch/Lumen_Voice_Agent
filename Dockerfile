# ─── Stage 1: Build Next.js frontend ───────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json* frontend/yarn.lock* ./
RUN npm install --legacy-peer-deps || npm install

COPY frontend/ .
RUN npm run build

# ─── Stage 2: Run FastAPI + serve frontend ──────────────────────────────
FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy Python dependency files
COPY pyproject.toml uv.lock ./

# Install Python dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY app/ ./app/
COPY config.yaml ./

# Copy built Next.js frontend from builder stage
COPY --from=frontend-builder /frontend/.next ./frontend/.next
COPY --from=frontend-builder /frontend/public ./frontend/public
COPY --from=frontend-builder /frontend/next.config.js* ./frontend/
COPY --from=frontend-builder /frontend/package.json ./frontend/

# Run FastAPI (which serves both API and frontend)
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
