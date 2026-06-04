FROM python:3.13-slim

# Create non-root user for running the server
RUN groupadd -r monitoreo && useradd -r -g monitoreo monitoreo

WORKDIR /app

# Install dependencies first (cached layer unless requirements change)
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

# Copy application code
COPY app/ ./app/

# Data and config directories are mounted as volumes — just declare them
VOLUME ["/data", "/config"]

EXPOSE 8080

# Run as non-root
USER monitoreo

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
