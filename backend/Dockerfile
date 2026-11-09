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

# Security updates Debian has released since the base image was built.
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Only the wheels verified above: no index, no resolving anything new.
COPY --from=builder /app/wheels /wheels
# pip goes too once the wheels are in: nothing needs it at run time, and it
# carries its own copies of urllib3, msgpack and setuptools, with their own
# vulnerabilities. (To debug inside a container: python -m ensurepip.)
RUN pip install --no-cache-dir --no-index --no-deps /wheels/*.whl && rm -rf /wheels && \
    pip uninstall -y pip

# Copy application code AFTER installing dependencies (better cache layer)
COPY . .
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]