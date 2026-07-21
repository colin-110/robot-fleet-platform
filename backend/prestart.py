import asyncio
import logging

from app.database import engine, Base
# Import models so Base.metadata knows about them
from app.models import Telemetry, RobotCommand

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prestart")

async def init_db():
    logger.info("Initializing database...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialization complete.")

if __name__ == "__main__":
    asyncio.run(init_db())
