import asyncio
from app.database import AsyncSessionLocal
from app.repositories.telemetry_repo import TelemetryRepository
from datetime import datetime, timezone

async def main():
    async with AsyncSessionLocal() as session:
        repo = TelemetryRepository(session)
        rows = await repo.get_recent(1)
        if rows:
            print(rows[0].timestamp, type(rows[0].timestamp))
        print(datetime.now(timezone.utc))

if __name__ == "__main__":
    asyncio.run(main())
