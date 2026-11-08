"""The server's copies of the wire format and the predictor must match the agent's."""

from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "app" / "mqtt"
AGENT = Path(__file__).resolve().parents[2] / "agent" / "fleetops_agent"


@pytest.mark.skipif(not AGENT.exists(), reason="the agent package is not in this checkout")
@pytest.mark.parametrize("module", ["wire.py", "predict.py"])
def test_server_copy_is_the_agents(module):
    assert (BACKEND / module).read_bytes() == (AGENT / module).read_bytes(), (
        f"backend/app/mqtt/{module} has drifted from agent/fleetops_agent/{module}; "
        "change the agent's and copy it over"
    )
