FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-index --find-links third_party/python-wheels -r requirements.txt || \
    pip install -r requirements.txt

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p data logs

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/status')" || exit 1

# Labels
LABEL maintainer="WeatherPi Contributors"
LABEL description="Open source local weather station with Python + FastAPI + SQLite"

# Default command
CMD ["python", "-m", "scripts.run_all"]
