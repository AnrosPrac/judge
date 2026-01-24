FROM python:3.11-slim

# Install compilers and security tools
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for running the service
RUN useradd -m -u 1000 judge && \
    mkdir -p /app /tmp && \
    chown -R judge:judge /app /tmp

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=judge:judge . .

# Create judge module directory structure
RUN mkdir -p judge/languages

# Switch to non-root user
USER judge

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')" || exit 1

# Run with production settings
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--log-level", "info"]