"""Per-robot device keys: issuing, using, rotating and revoking them.

``conftest.py`` overrides ``verify_fleet_key`` for the other suites. These
tests remove that override so the real key check runs.
"""

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import device_keys
from app.auth import verify_fleet_key
from app.config import get_settings
from app.main import app
from app.models import Robot
from app.security import ADMIN, VIEWER, issue_access_token
from tests.conftest import TestSessionLocal

settings = get_settings()
MASTER = {"X-API-Key": settings.telemetry_api_key}


@pytest.fixture(autouse=True)
def real_fleet_auth(monkeypatch):
    override = app.dependency_overrides.pop(verify_fleet_key, None)

    # On-demand key lookups normally use the app's own session pool; route
    # them through the per-test session so no connection outlives its loop.
    async def fetch_hash(robot_id):
        async with TestSessionLocal() as db:
            result = await db.execute(select(Robot.key_hash).where(Robot.id == robot_id))
            return result.scalar_one_or_none()

    monkeypatch.setattr(device_keys, "_fetch_hash", fetch_hash)
    yield
    if override is not None:
        app.dependency_overrides[verify_fleet_key] = override


_tick = itertools.count()


def reading(robot_id: int) -> dict:
    """A reading with a unique timestamp, so repeated posts are not duplicates."""
    ts = datetime.now(timezone.utc) + timedelta(microseconds=next(_tick))
    return {
        "robot_id": robot_id,
        "battery": 80.0,
        "temperature": 35.0,
        "speed": 1.0,
        "timestamp": ts.isoformat(),
    }


async def issue_key(client, robot_id: int) -> str:
    response = await client.post(f"/api/v1/robots/{robot_id}/key", headers=MASTER)
    assert response.status_code == 200, response.text
    return response.json()["key"]


# ── Key format ──────────────────────────────────────────────────────


def test_generated_key_names_its_robot():
    key = device_keys.generate(7)
    assert key.startswith("rk_7_")
    assert device_keys.robot_id_of(key) == 7


def test_generated_keys_are_unique():
    assert len({device_keys.generate(7) for _ in range(100)}) == 100


@pytest.mark.parametrize(
    "candidate",
    [None, "", "not-a-device-key", "rk_7", "rk_seven_" + "a" * 43, "rk_7_short"],
)
def test_malformed_keys_are_rejected(candidate):
    assert device_keys.robot_id_of(candidate) is None


@pytest.mark.asyncio
async def test_a_key_cannot_be_relabelled_for_another_robot():
    key = device_keys.generate(7)
    device_keys.set_hash(7, device_keys.hash_key(key))
    device_keys.set_hash(8, device_keys.hash_key(device_keys.generate(8)))

    relabelled = "rk_8_" + key.split("_", 2)[2]

    assert await device_keys.verify(key) == 7
    assert await device_keys.verify(relabelled) is None


# ── Using a key ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_device_key_can_ingest_for_its_own_robot(client):
    key = await issue_key(client, 5)

    response = await client.post("/api/v1/telemetry", json=reading(5), headers={"X-API-Key": key})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_device_key_cannot_ingest_for_another_robot(client):
    key = await issue_key(client, 5)

    response = await client.post("/api/v1/telemetry", json=reading(6), headers={"X-API-Key": key})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_batch_is_rejected_if_any_reading_is_for_another_robot(client):
    key = await issue_key(client, 5)

    response = await client.post(
        "/api/v1/telemetry/batch",
        json=[reading(5), reading(5), reading(6)],
        headers={"X-API-Key": key},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_device_key_cannot_post_events_for_another_robot(client):
    key = await issue_key(client, 5)

    response = await client.post(
        "/api/v1/events",
        json={"robot_id": 6, "message": "spoofed"},
        headers={"X-API-Key": key},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_device_key_cannot_claim_another_robots_commands(client):
    key = await issue_key(client, 5)

    own = await client.post("/api/v1/commands/5/claim", headers={"X-API-Key": key})
    other = await client.post("/api/v1/commands/6/claim", headers={"X-API-Key": key})

    assert own.status_code == 200
    assert other.status_code == 403


@pytest.mark.asyncio
async def test_device_key_cannot_update_another_robots_command(client):
    sent = await client.post("/api/v1/commands/6", json={"command_type": "STOP"})
    command_id = sent.json()["payload"]["command_id"]
    key = await issue_key(client, 5)

    response = await client.patch(
        f"/api/v1/commands/{command_id}/status",
        json={"status": "ACKNOWLEDGED"},
        headers={"X-API-Key": key},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_guessed_key_is_rejected(client):
    await issue_key(client, 5)

    response = await client.post(
        "/api/v1/telemetry",
        json=reading(5),
        headers={"X-API-Key": "rk_5_" + "A" * 43},
    )

    assert response.status_code == 401


# ── Rotation and revocation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_issuing_a_new_key_invalidates_the_old_one(client):
    old = await issue_key(client, 5)
    new = await issue_key(client, 5)

    old_response = await client.post(
        "/api/v1/telemetry", json=reading(5), headers={"X-API-Key": old}
    )
    new_response = await client.post(
        "/api/v1/telemetry", json=reading(5), headers={"X-API-Key": new}
    )

    assert old_response.status_code == 401
    assert new_response.status_code == 200


@pytest.mark.asyncio
async def test_revoked_key_stops_working(client):
    key = await issue_key(client, 5)

    revoke = await client.delete("/api/v1/robots/5/key", headers=MASTER)
    response = await client.post("/api/v1/telemetry", json=reading(5), headers={"X-API-Key": key})

    assert revoke.status_code == 204
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_revoked_key_stays_dead_after_a_new_key_is_issued(client):
    leaked = await issue_key(client, 7)
    await client.delete("/api/v1/robots/7/key", headers=MASTER)
    replacement = await issue_key(client, 7)

    old = await client.post("/api/v1/telemetry", json=reading(7), headers={"X-API-Key": leaked})
    new = await client.post(
        "/api/v1/telemetry", json=reading(7), headers={"X-API-Key": replacement}
    )

    assert old.status_code == 401
    assert new.status_code == 200


@pytest.mark.asyncio
async def test_only_the_hash_is_stored(client, db):
    key = await issue_key(client, 7)

    stored = (await db.execute(select(Robot.key_hash).where(Robot.id == 7))).scalar_one()

    assert stored == device_keys.hash_key(key)
    assert key not in stored


@pytest.mark.asyncio
async def test_device_keys_survive_a_master_key_rotation(client, monkeypatch):
    key = await issue_key(client, 7)
    monkeypatch.setattr(settings, "telemetry_api_key", "a-completely-new-master-key")

    response = await client.post("/api/v1/telemetry", json=reading(7), headers={"X-API-Key": key})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_revoking_an_unknown_robot_is_404(client):
    response = await client.delete("/api/v1/robots/999/key", headers=MASTER)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_issuing_a_key_registers_the_robot(client):
    await issue_key(client, 42)

    status = await client.get("/api/v1/robots/status")

    assert 42 in {robot["robot_id"] for robot in status.json()}


# ── Who may manage keys ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_keys_cannot_be_issued_without_credentials(client):
    response = await client.post("/api/v1/robots/5/key")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_device_key_cannot_issue_keys(client):
    key = await issue_key(client, 5)

    response = await client.post("/api/v1/robots/6/key", headers={"X-API-Key": key})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_account_can_issue_keys(client):
    token, _ = issue_access_token("root", ADMIN)

    response = await client.post(
        "/api/v1/robots/5/key", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_account_cannot_issue_keys(client):
    token, _ = issue_access_token("watcher", VIEWER)

    response = await client.post(
        "/api/v1/robots/5/key", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


# ── Shared master key ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_master_key_still_ingests_by_default(client):
    response = await client.post("/api/v1/telemetry", json=reading(5), headers=MASTER)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_master_key_can_be_refused_for_ingest(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_shared_ingest_key", False)
    key = await issue_key(client, 5)

    shared = await client.post("/api/v1/telemetry", json=reading(5), headers=MASTER)
    device = await client.post("/api/v1/telemetry", json=reading(5), headers={"X-API-Key": key})

    assert shared.status_code == 401
    assert device.status_code == 200


# ── Cache across instances ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_instance_never_queries_the_database(monkeypatch):
    async def fetch(robot_id):
        raise AssertionError("should not query the database")

    monkeypatch.setattr(device_keys, "_fetch_hash", fetch)
    monkeypatch.setattr(settings, "websocket_backend", "direct")

    assert await device_keys.verify(device_keys.generate(5)) is None


@pytest.mark.asyncio
async def test_key_issued_by_another_instance_is_accepted_after_one_lookup(monkeypatch):
    key = device_keys.generate(5)
    lookups = []

    async def fetch(robot_id):
        lookups.append(robot_id)
        return device_keys.hash_key(key)

    monkeypatch.setattr(device_keys, "_fetch_hash", fetch)
    monkeypatch.setattr(settings, "websocket_backend", "mqtt")

    assert await device_keys.verify(key) == 5
    assert await device_keys.verify(key) == 5
    assert lookups == [5]


@pytest.mark.asyncio
async def test_lookups_are_rate_limited_per_robot(monkeypatch):
    lookups = []

    async def fetch(robot_id):
        lookups.append(robot_id)
        return None

    monkeypatch.setattr(device_keys, "_fetch_hash", fetch)
    monkeypatch.setattr(settings, "websocket_backend", "mqtt")

    for _ in range(5):
        assert await device_keys.verify(device_keys.generate(5)) is None

    assert lookups == [5]
