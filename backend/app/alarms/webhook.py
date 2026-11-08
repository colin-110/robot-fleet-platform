"""Alarm webhooks: every raise, acknowledgement and clear, POSTed as JSON.

Deliveries go through a bounded queue and a single sender, so a slow or
failing receiver never holds up alarm evaluation. Each delivery is tried up
to ``ATTEMPTS`` times with backoff. The body is signed with HMAC-SHA256 under
``ALARM_WEBHOOK_SECRET``; receivers should check ``X-FleetOps-Signature``
(``sha256=<hex digest of the raw body>``) before trusting it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import urllib.request

import orjson

logger = logging.getLogger(__name__)

ATTEMPTS = 4
BACKOFF_SECONDS = (1.0, 4.0, 16.0)
TIMEOUT_SECONDS = 5.0
MAX_QUEUED = 1000


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(url: str, body: bytes, headers: dict) -> int:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return response.status


class WebhookSender:
    def __init__(self, url: str, secret: str, backoff=BACKOFF_SECONDS) -> None:
        self.url = url
        self.secret = secret
        self.backoff = backoff
        self.queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(MAX_QUEUED)
        self.stats = {"delivered": 0, "failed": 0, "dropped": 0}

    async def listener(self, event: str, alarm: dict) -> None:
        """Alarm engine listener: queue the delivery and return at once."""
        body = orjson.dumps({"event": event, "alarm": alarm})
        if self.queue.full():
            self.queue.get_nowait()
            self.stats["dropped"] += 1
            logger.warning("Webhook queue full; dropped the oldest delivery")
        self.queue.put_nowait((event, body))

    async def deliver(self, event: str, body: bytes) -> bool:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "FleetOps-Alarms",
            "X-FleetOps-Event": event,
            "X-FleetOps-Signature": signature(self.secret, body),
        }
        for attempt in range(ATTEMPTS):
            try:
                status = await asyncio.to_thread(_post, self.url, body, headers)
                if 200 <= status < 300:
                    self.stats["delivered"] += 1
                    return True
            except Exception as exc:
                logger.info("Webhook attempt %d failed: %s", attempt + 1, exc)
            if attempt < ATTEMPTS - 1:
                await asyncio.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
        self.stats["failed"] += 1
        logger.warning("Webhook delivery of %s failed after %d attempts", event, ATTEMPTS)
        return False

    async def run(self) -> None:
        while True:
            event, body = await self.queue.get()
            await self.deliver(event, body)
