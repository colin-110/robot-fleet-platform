import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.alarms.webhook import WebhookSender, signature


class Receiver:
    """A local HTTP server that fails the first ``fail`` requests."""

    def __init__(self, fail: int = 0):
        self.requests = []
        self.fail = fail
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((self.headers, body))  # case-insensitive
                status = 500 if outer.fail > 0 else 204
                outer.fail -= 1
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def receiver():
    made = []

    def make(fail=0):
        made.append(Receiver(fail))
        return made[-1]

    yield make
    for r in made:
        r.close()


ALARM = {"id": 1, "robot_id": 3, "kind": "BATTERY_LOW", "state": "ACTIVE"}


async def test_a_delivery_is_signed(receiver):
    r = receiver()
    sender = WebhookSender(r.url, "s3cret", backoff=(0.01,))
    await sender.listener("raised", ALARM)
    event, body = sender.queue.get_nowait()
    assert await sender.deliver(event, body) is True
    headers, received = r.requests[0]
    assert json.loads(received) == {"event": "raised", "alarm": ALARM}
    assert headers["X-FleetOps-Event"] == "raised"
    assert headers["X-FleetOps-Signature"] == signature("s3cret", received)
    assert headers["X-FleetOps-Signature"] != signature("other", received)


async def test_a_failing_receiver_is_retried(receiver):
    r = receiver(fail=2)
    sender = WebhookSender(r.url, "s", backoff=(0.01,))
    assert await sender.deliver("raised", b"{}") is True
    assert len(r.requests) == 3
    assert sender.stats["delivered"] == 1


async def test_a_dead_receiver_gives_up(receiver):
    r = receiver(fail=100)
    sender = WebhookSender(r.url, "s", backoff=(0.01,))
    assert await sender.deliver("raised", b"{}") is False
    assert len(r.requests) == 4
    assert sender.stats["failed"] == 1


async def test_the_queue_is_bounded(monkeypatch):
    monkeypatch.setattr("app.alarms.webhook.MAX_QUEUED", 2)
    sender = WebhookSender("http://127.0.0.1:9/", "s")
    for i in range(3):
        await sender.listener("raised", {**ALARM, "id": i})
    assert sender.queue.qsize() == 2
    assert sender.stats["dropped"] == 1


async def test_the_sender_drains_the_queue(receiver):
    r = receiver()
    sender = WebhookSender(r.url, "s", backoff=(0.01,))
    task = asyncio.create_task(sender.run())
    for event in ("raised", "acknowledged", "cleared"):
        await sender.listener(event, ALARM)
    for _ in range(100):
        if len(r.requests) == 3:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    assert [h["X-FleetOps-Event"] for h, _ in r.requests] == ["raised", "acknowledged", "cleared"]
