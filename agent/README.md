# fleetops-agent

The robot side of FleetOps: a small Python package that sends a robot's
telemetry, events and command progress to the FleetOps broker over MQTT.

- **Compact.** CBOR with short keys, fixed-point numbers and only the fields
  that changed (`fleetops_agent/wire.py`). A reading is about 130 bytes on
  its own and 20 to 35 inside a batch, against about 450 as JSON.
- **Nothing lost on a crash.** Everything sent goes through a bounded SQLite
  outbox (`fleetops_agent/outbox.py`) and stays there until the server
  confirms it is stored, so a killed robot process resends it on restart.
- **Confirmed end to end.** The server publishes how far each robot's outbox
  is committed (`fleet/<id>/stored`). A broker acknowledgement is not enough,
  because a broker can lose what it accepted; anything the server does not
  confirm is sent again.
- **Remote settings.** The server's desired settings arrive on a retained
  topic; the agent applies the ones it knows, keeps them on disk, calls
  `on_config` and reports what it applied.
- **Commands run once.** Command ids are remembered on disk, so a command
  pushed again after a reconnect or a restart is not run twice.

## Use

~~~python
from fleetops_agent.agent import Agent

agent = Agent(
    7,
    device_key,
    "broker.example.com",
    8883,
    tls=True,  # verified against the system's CAs; ca_certs= for your own
    outbox_path="/var/lib/robot/outbox.db",
)
agent.start()

# Every sensor tick: sent only when it strays from the prediction the server
# shares (or a discrete field changes, or the heartbeat is due).
agent.observe({"battery": 81.5, "temperature": 39.2, "speed": 0.8, "x": 3.1, "y": -2.0})
agent.send_event("Mission M-00042 complete")

for command in agent.take_commands():
    agent.report_command(command["id"], "ACKNOWLEDGED")
    ...
    agent.report_command(command["id"], "COMPLETED")

agent.stop()
~~~

## Test

~~~bash
pip install -e '.[test]'
docker run -d -p 1883:1883 eclipse-mosquitto:2.0.22 mosquitto -c /mosquitto-no-auth.conf
MQTT_TEST_HOST=localhost pytest
~~~
