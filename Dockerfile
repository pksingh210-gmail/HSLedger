# =========================
# Stage 1 — Base Image
# =========================
FROM python:3.13-slim AS base

# Environment settings
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Set working directory
WORKDIR /app

# Install system dependencies (needed by pandas, openpyxl, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# =========================
# Stage 2 — Install Python Dependencies
# =========================
# Copy dependency file first (to leverage Docker caching)
COPY requirements.txt ./

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# =========================
# Stage 3 — Copy Application Code
# =========================
COPY . .

# Streamlit configuration for Northflank
ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ENABLECORS=false \
    STREAMLIT_SERVER_ENABLEXSRSFPROTECTION=false \
    STREAMLIT_SERVER_PORT=8080 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

# =========================
# Stage 4 — Expose and Run
# =========================
EXPOSE 8080

# Healthcheck for Northflank
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s \
  CMD curl -f http://localhost:8080/_stcore/health || exit 1

# Default command
CMD ["streamlit", "run", "app.py"]
