"""Prometheus metrics, served at ``/metrics``.

Most numbers already exist as plain counters on the objects that own them
(the ingest consumer's ``stats``, the WebSocket manager, the alarm engine), so
a collector reads them when Prometheus scrapes and the hot path does no extra
work. Only durations are recorded as they happen, once per batch or pass.

The lifespan registers what is running in ``sources``; anything missing (MQTT
off, webhooks not configured) is simply left out of the scrape.
"""

from __future__ import annotations

import time

from prometheus_client import REGISTRY, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

# name -> object, set by the app lifespan: "ingest", "rollups", "webhook".
sources: dict[str, object] = {}

INGEST_BATCH_SECONDS = Histogram(
    "fleetops_ingest_batch_seconds",
    "Time to decode, store, broadcast and acknowledge one ingest batch.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
INGEST_BATCH_MESSAGES = Histogram(
    "fleetops_ingest_batch_messages",
    "MQTT messages in one ingest batch.",
    buckets=(1, 5, 10, 25, 50, 100, 200, 500, 1000, 2000),
)
READING_AGE_SECONDS = Histogram(
    "fleetops_reading_age_seconds",
    "From a reading's device timestamp to its commit: network, broker queue "
    "and outbox time together (readings held through an outage land high).",
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 1800, 3600),
)
ALARM_PASS_SECONDS = Histogram(
    "fleetops_alarm_pass_seconds",
    "Time for one alarm evaluation over every robot.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
ROLLUP_RUN_SECONDS = Histogram(
    "fleetops_rollup_run_seconds",
    "Time for one rollup run.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)


def observe_reading_ages(readings, now: float | None = None) -> None:
    now = time.time() if now is None else now
    for row in readings:
        READING_AGE_SECONDS.observe(max(0.0, now - row.timestamp.timestamp()))


def _counter(name: str, doc: str, value: float) -> CounterMetricFamily:
    return CounterMetricFamily(name, doc, value=value)


def _gauge(name: str, doc: str, value: float) -> GaugeMetricFamily:
    return GaugeMetricFamily(name, doc, value=value)


INGEST_COUNTERS = {
    "stored": "Readings newly stored.",
    "events": "Events newly stored.",
    "statuses": "Command status reports applied.",
    "duplicates": "Readings and events skipped as already stored (redeliveries).",
    "rejected": "Messages or items dropped as malformed or refused.",
    "batches": "Ingest batches processed.",
    "gaps": "Envelopes that arrived after a gap in their robot's sequence.",
    "given_up": "Items robots' outboxes dropped before they were stored.",
}


class FleetCollector:
    """Reads the live counters and sizes at scrape time."""

    def collect(self):
        from app import presence
        from app.alarms import engine as alarm_engine
        from app.intelligence import fleet
        from app.websocket_manager import manager

        ingest = sources.get("ingest")
        if ingest is not None:
            for key, doc in INGEST_COUNTERS.items():
                yield _counter(f"fleetops_ingest_{key}", doc, ingest.stats[key])
            yield _gauge(
                "fleetops_ingest_queue_depth",
                "Messages received from the broker and waiting for a batch.",
                ingest.queue_depth,
            )
            yield _gauge(
                "fleetops_ingest_connected",
                "1 while the ingest consumer is connected to the broker.",
                float(ingest.connected.is_set()),
            )

        ids = presence.robot_ids()
        online = sum(1 for r in ids if not presence.is_offline(r))
        yield _gauge("fleetops_robots_online", "Robots whose presence is online.", online)
        yield _gauge(
            "fleetops_robots_modelled", "Robots with an in-memory model.", len(fleet.robots())
        )

        yield _gauge(
            "fleetops_websocket_clients", "Connected dashboards.", manager.connection_count
        )
        yield _gauge(
            "fleetops_websocket_queued_frames",
            "Frames waiting in all dashboards' outbound queues.",
            manager.queued_frames,
        )
        yield _counter(
            "fleetops_websocket_frames_sent", "Frames sent to dashboards.", manager.frames_sent
        )
        yield _counter(
            "fleetops_websocket_frames_dropped",
            "Frames dropped because a dashboard could not keep up.",
            manager.frames_dropped,
        )

        engine = alarm_engine.engine
        if engine is not None and engine.open_count is not None:
            yield _gauge("fleetops_alarms_open", "Alarms not yet cleared.", engine.open_count)

        rollups = sources.get("rollups")
        if rollups is not None:
            yield _counter("fleetops_rollup_runs", "Rollup runs.", rollups.stats["runs"])
            yield _counter(
                "fleetops_rollup_minutes_written",
                "Robot-minutes of rollups written.",
                rollups.stats["minutes_written"],
            )

        webhook = sources.get("webhook")
        if webhook is not None:
            for key in ("delivered", "failed", "dropped"):
                yield _counter(
                    f"fleetops_webhook_{key}", f"Alarm webhooks {key}.", webhook.stats[key]
                )


REGISTRY.register(FleetCollector())
