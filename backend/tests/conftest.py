"""
Shared test fixtures for the robot fleet backend.

Uses an in-memory SQLite database for fast, isolated tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.cache import cache

# ── In-memory SQLite for tests ──────────────────────────────────────

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

TestSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
    class_=AsyncSession,
)


async def override_get_db():
    """Dependency override that yields a test database session."""
    async with TestSessionLocal() as session:
        yield session


# Override the DB dependency for all tests
app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_database():
    import asyncio
    async def init_db():
        async with test_engine.begin() as conn:
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
    """Mock Redis to avoid event loop issues and cache pollution."""
    from app.websocket_manager import manager
    from app.cache import cache

    async def mock_broadcast(*args, **kwargs):
        pass
    monkeypatch.setattr(manager, "broadcast", mock_broadcast)

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

@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def db():
    """Raw database session for direct DB operations in tests."""
    return TestSessionLocal()


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
