import asyncio
import logging

from app.database import engine, Base
# Import models so Base.metadata knows about them
from app.models import Telemetry, RobotCommand, Robot, Event

import subprocess

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prestart")

async def init_db():
    logger.info("Initializing database...")
    # Run alembic upgrade head using subprocess
    try:
        subprocess.run(["alembic", "upgrade", "head"], check=True)
        logger.info("Database initialization complete.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error running alembic migrations: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(init_db())
