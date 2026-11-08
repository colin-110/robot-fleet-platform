"""Authentication and ACL callbacks for the MQTT broker.

mosquitto-go-auth calls these with JSON bodies and treats a 2xx status as
"allowed" and anything else as "denied" (``auth_opt_http_response_mode
status``). They are not part of the public API and are left out of the schema.

- **Robots** log in with username ``robot-<id>`` and their device key as the
  password. The broker replaces every client id with the username
  (``use_username_as_clientid``), so a robot cannot connect under another
  robot's client id and take over its session. The ACL check also requires the
  two to match, in case that setting is ever lost.
- **The API** logs in as ``MQTT_SERVICE_USERNAME`` (or ``<that>-<name>`` for
  extra connections) with the master key and is a superuser.
- **ACL checks** confine a robot to its own topics (``app/mqtt/topics.py``) and
  deny everything once its key is revoked. The broker caches results briefly,
  so a revocation takes effect for connected robots within that cache window.
"""

import logging

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app import device_keys
from app.auth import is_valid_api_key
from app.config import get_settings
from app.mqtt import topics

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/internal/mqtt", include_in_schema=False)

ALLOW = 200
DENY = 403


class LoginCheck(BaseModel):
    username: str = ""
    password: str = ""
    clientid: str = ""


class SuperuserCheck(BaseModel):
    username: str = ""


class AclCheck(BaseModel):
    username: str = ""
    clientid: str = ""
    topic: str = ""
    acc: int = 0


def _is_service(username: str) -> bool:
    base = settings.mqtt_service_username
    return username == base or username.startswith(f"{base}-")


@router.post("/user")
async def check_login(body: LoginCheck) -> Response:
    if _is_service(body.username):
        allowed = is_valid_api_key(body.password)
    else:
        robot_id = topics.robot_id_from_username(body.username)
        allowed = robot_id is not None and await device_keys.verify(body.password) == robot_id
    if not allowed:
        logger.warning("MQTT login refused for %r", body.username)
    return Response(status_code=ALLOW if allowed else DENY)


@router.post("/superuser")
async def check_superuser(body: SuperuserCheck) -> Response:
    return Response(status_code=ALLOW if _is_service(body.username) else DENY)


@router.post("/acl")
async def check_acl(body: AclCheck) -> Response:
    robot_id = topics.robot_id_from_username(body.username)
    allowed = (
        robot_id is not None
        and body.clientid == body.username
        and device_keys.has_key(robot_id)
        and topics.robot_may(robot_id, body.topic, body.acc)
    )
    return Response(status_code=ALLOW if allowed else DENY)
