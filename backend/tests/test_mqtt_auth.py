"""The broker's login, superuser and ACL callbacks."""

import pytest
from sqlalchemy import select

from app import device_keys
from app.config import get_settings
from app.models import Robot
from tests.conftest import TestSessionLocal

settings = get_settings()
MASTER = {"X-API-Key": settings.telemetry_api_key}
SERVICE = settings.mqtt_service_username


@pytest.fixture(autouse=True)
def test_key_lookups(monkeypatch):
    async def fetch_hash(robot_id):
        async with TestSessionLocal() as db:
            result = await db.execute(select(Robot.key_hash).where(Robot.id == robot_id))
            return result.scalar_one_or_none()

    monkeypatch.setattr(device_keys, "_fetch_hash", fetch_hash)


async def issue_key(client, robot_id: int) -> str:
    response = await client.post(f"/api/v1/robots/{robot_id}/key", headers=MASTER)
    assert response.status_code == 200, response.text
    return response.json()["key"]


async def login(client, username, password, clientid=None):
    body = {"username": username, "password": password, "clientid": clientid or username}
    return (await client.post("/internal/mqtt/user", json=body)).status_code


async def acl(client, username, topic, acc, clientid=None):
    body = {"username": username, "clientid": clientid or username, "topic": topic, "acc": acc}
    return (await client.post("/internal/mqtt/acl", json=body)).status_code


@pytest.mark.asyncio
async def test_robot_logs_in_with_its_device_key(client):
    key = await issue_key(client, 5)
    assert await login(client, "robot-5", key) == 200


@pytest.mark.asyncio
async def test_robot_cannot_log_in_as_another_robot(client):
    key = await issue_key(client, 5)
    await issue_key(client, 6)
    assert await login(client, "robot-6", key) == 403


@pytest.mark.asyncio
async def test_bad_and_revoked_keys_are_refused(client):
    key = await issue_key(client, 5)
    assert await login(client, "robot-5", key[:-1] + "x") == 403
    await client.delete("/api/v1/robots/5/key", headers=MASTER)
    assert await login(client, "robot-5", key) == 403


@pytest.mark.asyncio
async def test_master_key_only_works_for_the_service_account(client):
    assert await login(client, SERVICE, settings.telemetry_api_key) == 200
    assert await login(client, f"{SERVICE}-watch", settings.telemetry_api_key) == 200
    assert await login(client, SERVICE, "wrong") == 403
    assert await login(client, "robot-5", settings.telemetry_api_key) == 403


@pytest.mark.asyncio
async def test_only_the_service_account_is_a_superuser(client):
    ok = await client.post("/internal/mqtt/superuser", json={"username": SERVICE})
    extra = await client.post("/internal/mqtt/superuser", json={"username": f"{SERVICE}-watch"})
    robot = await client.post("/internal/mqtt/superuser", json={"username": "robot-5"})
    lookalike = await client.post("/internal/mqtt/superuser", json={"username": f"{SERVICE}x"})
    codes = (ok.status_code, extra.status_code, robot.status_code, lookalike.status_code)
    assert codes == (200, 200, 403, 403)


@pytest.mark.asyncio
async def test_acl_confines_a_robot_to_its_own_topics(client):
    await issue_key(client, 5)
    assert await acl(client, "robot-5", "fleet/5/telemetry", 2) == 200
    assert await acl(client, "robot-5", "fleet/5/commands", 4) == 200
    assert await acl(client, "robot-5", "fleet/6/telemetry", 2) == 403
    assert await acl(client, "robot-5", "fleet/5/commands", 2) == 403
    assert await acl(client, "robot-5", "fleet/5/telemetry", 2, clientid="robot-6") == 403


@pytest.mark.asyncio
async def test_acl_denies_everything_after_revocation(client):
    await issue_key(client, 5)
    await client.delete("/api/v1/robots/5/key", headers=MASTER)
    assert await acl(client, "robot-5", "fleet/5/telemetry", 2) == 403


@pytest.mark.asyncio
async def test_malformed_requests_are_denied(client):
    assert (await client.post("/internal/mqtt/user", json={})).status_code == 403
    assert (await client.post("/internal/mqtt/acl", json={})).status_code == 403
