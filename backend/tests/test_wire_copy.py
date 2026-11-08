"""The server's copy of the wire format must match the agent's exactly."""

from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "app" / "mqtt" / "wire.py"
AGENT = Path(__file__).resolve().parents[2] / "agent" / "fleetops_agent" / "wire.py"


@pytest.mark.skipif(not AGENT.exists(), reason="the agent package is not in this checkout")
def test_server_wire_format_is_the_agents():
    assert SERVER.read_bytes() == AGENT.read_bytes(), (
        "backend/app/mqtt/wire.py has drifted from agent/fleetops_agent/wire.py; "
        "change the agent's and copy it over"
    )
