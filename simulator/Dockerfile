# Built from the repository root (it installs ../agent):
#   docker build -f simulator/Dockerfile .
FROM python:3.10-slim

WORKDIR /app

# The agent package first, then the simulator's own dependencies.
COPY agent/ /tmp/agent/
COPY simulator/requirements.txt .
RUN pip install --no-cache-dir /tmp/agent -r requirements.txt && rm -rf /tmp/agent

COPY simulator/robot_sim.py ./

# We use the python unbuffered flag so logs show up immediately in docker-compose logs
ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "robot_sim.py", "--api-url", "http://backend:8000/api/v1/telemetry"]
