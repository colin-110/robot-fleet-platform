# Built from the repository root (it installs ../agent):
#   docker build -f simulator/Dockerfile .
FROM python:3.13-slim

WORKDIR /app

# Security updates Debian has released since the base image was built.
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Every dependency, the agent's included, at the version and hash in the lock.
# Regenerate it from the repository root after changing either input:
#   pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
#     --output-file simulator/requirements.lock simulator/requirements.txt agent/pyproject.toml
COPY simulator/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
# Then the agent itself, whose dependencies are already in place.
COPY agent/ /tmp/agent/
# pip is not needed at run time and carries vulnerable copies of urllib3,
# msgpack and setuptools.
RUN pip install --no-cache-dir --no-deps /tmp/agent && rm -rf /tmp/agent && \
    pip uninstall -y pip

COPY simulator/robot_sim.py ./

# We use the python unbuffered flag so logs show up immediately in docker-compose logs
ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "robot_sim.py", "--api-url", "http://backend:8000/api/v1/telemetry"]
