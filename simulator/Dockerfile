FROM python:3.10-slim

WORKDIR /app

# Install dependencies first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy simulator code
COPY robot_sim.py .

# We use the python unbuffered flag so logs show up immediately in docker-compose logs
ENV PYTHONUNBUFFERED=1

# Default command: run 50 robots pointing at the internal backend service
CMD ["python", "-u", "robot_sim.py", "--api-url", "http://backend:8000/api/v1/telemetry"]
