"""
Shared test fixtures for the robot fleet backend.

These tests run against **PostgreSQL**, not SQLite: the application relies on
Postgres-specific SQL (``date_trunc``, ``INTERVAL`` arithmetic, and atomic
``UPDATE ... WHERE status='PENDING'`` dispatch) that SQLite cannot execute.
CI provisions a dedicated ``fleet_test_db`` Postgres service and points
``DATABASE_URL`` at it (see ``.github/workflows/ci.yml``). To run locally,
export ``DATABASE_URL`` to a throwaway Postgres database whose name contains
``test`` — for example::

    createdb fleet_test_db
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/fleet_test_db \
        pytest
"""

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.database import Base, get_db
from app.main import app
from app.cache import cache
from app.auth import verify_api_key
from app.config import get_settings

# ── Resolve the Postgres test database ──────────────────────────────

_RAW_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/fleet_test_db",
)

# Safety guard: this suite calls ``drop_all`` between tests. Refuse to run
# against any database whose name doesn't look like a test database so we can
# never accidentally wipe a development or production schema.
_DB_NAME = _RAW_URL.rsplit("/", 1)[-1].split("?")[0]
if "test" not in _DB_NAME.lower():
    raise RuntimeError(
        f"Refusing to run the test suite against database {_DB_NAME!r}: its name "
        "must contain 'test'. Point DATABASE_URL at a throwaway test database."
    )

TEST_DATABASE_URL = _RAW_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# NullPool: each test recreates the schema, so we don't want connections held
# open across the create/drop cycle.
test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

TestSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=test_engine,
    class_=AsyncSession,
)

# Telemetry ingestion has two code paths; tests assert on synchronous DB writes,
# so pin the buffer off regardless of the ambient environment.
get_settings().use_redis_buffer = False


async def override_get_db():
    """Dependency override that yields a test database session."""
    async with TestSessionLocal() as session:
        yield session


# Route the app at the test database and disable API-key auth for tests. The
# auth mechanism is trivial (a single shared key) and isn't the unit under test
# here; overriding it keeps every test from having to carry the header.
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: None


@pytest.fixture(autouse=True)
def setup_database():
    import asyncio

    async def init_db():
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await cache.clear()

    async def drop_db():
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    asyncio.run(init_db())
    yield
    asyncio.run(drop_db())


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    """Mock Redis so tests don't need a live broadcast/cache backend."""
    from app.websocket_manager import manager
    from app.cache import cache
    from app import middleware

    async def mock_broadcast(*args, **kwargs):
        pass
    monkeypatch.setattr(manager, "broadcast", mock_broadcast)
    monkeypatch.setattr(middleware, "get_redis", lambda: None)

    # In-memory mock for cache
    _store = {}

    async def mock_get(key):
        return _store.get(key)

    async def mock_set(key, value, ttl_seconds=5.0):
        _store[key] = value

    async def mock_clear():
        _store.clear()

    monkeypatch.setattr(cache, "get", mock_get)
    monkeypatch.setattr(cache, "set", mock_set)
    monkeypatch.setattr(cache, "clear", mock_clear)


@pytest_asyncio.fixture
async def client():
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture
async def db():
    """Raw database session for direct DB assertions in tests.

    Yields inside a context manager so the session (and its connection) is
    always closed/rolled back after the test. Leaving it open would hold an
    'idle in transaction' lock that blocks the teardown ``DROP TABLE``.
    """
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
def sample_telemetry():
    """Sample telemetry payload for POST requests."""
    return {
        "robot_id": 1,
        "battery": 85.5,
        "temperature": 38.2,
        "speed": 1.1,
        "status": "ACTIVE",
        "mission_id": "M-00001",
        "mission_type": "PATROL",
        "mission_progress": 100.0,
        "battery_health": 92.0,
        "motor_health": 88.0,
        "sensor_health": 95.0,
        "network_health": 90.0,
        "x": 5.2,
        "y": -3.1,
    }


@pytest.fixture
def sample_telemetry_low_battery():
    """Telemetry payload with critically low battery."""
    return {
        "robot_id": 2,
        "battery": 8.0,
        "temperature": 42.5,
        "speed": 0.0,
        "status": "LOW POWER",
        "battery_health": 65.0,
        "motor_health": 72.0,
        "sensor_health": 80.0,
        "network_health": 75.0,
        "x": 0.0,
        "y": 0.0,
    }


@pytest.fixture
def sample_telemetry_overheating():
    """Telemetry payload with dangerous temperature."""
    return {
        "robot_id": 3,
        "battery": 60.0,
        "temperature": 105.0,
        "speed": 0.5,
        "status": "OVERHEATING",
        "battery_health": 80.0,
        "motor_health": 40.0,
        "sensor_health": 70.0,
        "network_health": 85.0,
        "x": 10.0,
        "y": 10.0,
    }
