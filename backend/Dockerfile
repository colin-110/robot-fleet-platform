# Build stage
FROM python:3.13-slim AS builder
WORKDIR /app
# Exactly the versions in the lock, each checked against its hash, so every
# build installs the same packages and a tampered download is refused.
# Regenerate it after changing requirements.txt:
#   pip-compile --generate-hashes --allow-unsafe --strip-extras \
#     --no-emit-index-url --output-file requirements.lock requirements.txt
COPY requirements.lock .
RUN pip wheel --no-cache-dir --require-hashes --wheel-dir /app/wheels -r requirements.lock

# Runtime stage
FROM python:3.13-slim
WORKDIR /app

# No .pyc files: the image is read-only in practice and every module imported
# would otherwise write a cache file that ships in the layer for nothing.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd -m appuser

# Install curl for healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Only the wheels verified above: no index, no resolving anything new.
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache-dir --no-index --no-deps /wheels/*.whl && rm -rf /wheels

# Copy application code AFTER installing dependencies (better cache layer)
COPY . .
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]