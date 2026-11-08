# fleetops-agent

The robot side of FleetOps: a small Python package that sends a robot's
telemetry, events and command progress to the FleetOps broker over MQTT.

- **Compact.** CBOR with short keys, fixed-point numbers and only the fields
  that changed (`fleetops_agent/wire.py`). A reading is about 130 bytes on
  its own and 20 to 35 inside a batch, against about 450 as JSON.
