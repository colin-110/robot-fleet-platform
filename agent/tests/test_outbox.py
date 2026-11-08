import os
import signal
import subprocess
import sys
import textwrap

import pytest
from fleetops_agent.outbox import Outbox


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "outbox.db")


def test_items_come_back_in_order_with_their_channel(path):
    box = Outbox(path)
    assert box.put_many("telemetry", [{"b": 1}, {"b": 2}]) == [1, 2]
    assert box.put("events", {"m": "x"}) == 3
    assert box.read(after=0, limit=10) == [
        (1, "telemetry", {"b": 1}),
        (2, "telemetry", {"b": 2}),
        (3, "events", {"m": "x"}),
    ]
    assert box.read(after=1, limit=1) == [(2, "telemetry", {"b": 2})]
    assert len(box) == 3


def test_confirm_removes_up_to_and_moves_the_floor(path):
    box = Outbox(path)
    box.put_many("telemetry", [{"b": i} for i in range(5)])
    assert box.floor() == 1
    assert box.confirm(3) == 3
    assert [seq for seq, _, _ in box.read(0, 10)] == [4, 5]
    assert box.floor() == 4
    box.confirm(5)
    # Empty: the floor is the next number, so the server knows nothing is held.
    assert box.floor() == 6


def test_numbers_are_never_reused_after_a_restart(path):
    box = Outbox(path)
    box.put_many("telemetry", [{"b": i} for i in range(3)])
    box.confirm(3)
    box.close()

    reopened = Outbox(path)
    assert reopened.put("telemetry", {"b": 9}) == 4
    assert reopened.floor() == 4


def test_the_stream_id_belongs_to_the_file(path, tmp_path):
    first = Outbox(path).stream
    assert Outbox(path).stream == first
    assert Outbox(str(tmp_path / "other.db")).stream != first


def test_a_full_outbox_drops_the_oldest(path):
    box = Outbox(path, max_items=3)
    box.put_many("telemetry", [{"b": i} for i in range(5)])
    assert len(box) == 3
    assert box.dropped == 2
    assert box.floor() == 3
    assert [item["b"] for _, _, item in box.read(0, 10)] == [2, 3, 4]


def test_items_survive_the_process_being_killed(path):
    script = textwrap.dedent(
        f"""
        import os, signal
        from fleetops_agent.outbox import Outbox
        box = Outbox({path!r})
        for i in range(200):
            box.put("telemetry", {{"b": i}})
        print("written", flush=True)
        os.kill(os.getpid(), signal.SIGKILL)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.getcwd()},
    )
    assert result.returncode == -signal.SIGKILL
    assert "written" in result.stdout
    box = Outbox(path)
    assert len(box) == 200
    assert box.read(199, 5) == [(200, "telemetry", {"b": 199})]


def test_a_command_is_seen_once_even_across_restarts(path):
    box = Outbox(path)
    assert box.first_sighting("c-1", 1) is True
    assert box.first_sighting("c-1", 2) is False
    box.close()
    assert Outbox(path).first_sighting("c-1", 3) is False


def test_meta_values_persist(path):
    box = Outbox(path)
    assert box.get_meta("config") is None
    box.set_meta("config", {"version": 2, "values": {"report_interval_s": 5.0}})
    box.close()
    assert Outbox(path).get_meta("config") == {"version": 2, "values": {"report_interval_s": 5.0}}


def test_synchronous_mode_is_checked(path):
    with pytest.raises(ValueError):
        Outbox(path, synchronous="OFF")
