# WORK_LOG — Person A (Vinith: vision & intelligence)

Append-only. Only Person A writes here. Dated entries; every number comes with the
command that produced it. History before the two-person split: `WORK_LOG_HISTORY.md`.

---

## 2026-09-24 — PR-0 "contracts" (branch `a/pr0-contracts`)

Implements research/26 §3 so both people can build against the same contract.

### Changes
- **Event schema v2** (`core/events.py`, `SCHEMA_VERSION = 2`, additive only): new types
  `SHELF_MOTION, CAMERA_MOUNT, BEAM_CROSS, PRESENCE, ENVIRONMENT, WEIGHT, NODE_HEALTH,
  CAMERA_HEALTH`; `HealthData` gains optional `power_w, mj_per_frame, throttled`. All v1 types
  and fields unchanged; a stored v1 event still validates (tested).
- **MQTT bus** (`core/bus.py`): paho-mqtt 2.x `CallbackAPIVersion.VERSION2`; retained Last Will
  `offline` on `storemind/{store}/{node}/status`, retained `online` on connect, explicit
  `offline` on clean close; QoS/retain table from §3.2 (`QOS1_TYPES`, `RETAINED_TYPES`);
  `listen=True` delivers other processes' events locally and drops the broker echo of our own
  by event id; `command_topic()` / `status_topic()`. `requirements.txt` pins `paho-mqtt>=2,<3`.
  `pipeline.py`: one-line change to pass `store`, `node`, `listen` to `MqttBus`.
- **Config** (`core/config.py`): `sensors.node` (`id`, `enabled_sensors`, `protocol_mode`,
  `time_sync_s`, `cmd_timeout_ms`, `cmd_retries`), `sensors.mems_nodes`, `sensors.has()`,
  `mqtt.listen`; readable validation errors naming the key; `load_secrets()` (file + env vars);
  `configs/secrets.example.yaml`.
- **Serial protocol**: `docs/PROTOCOL.md` + pure-Python `storemind/sensors/protocol.py`
  (framing, XOR checksum, 96-byte limit, typed decode incl. scaled ints and empty = not measured,
  `LineAssembler`, `SeqTracker`). All example lines were produced by the encoder.
- **Docs**: `docs/INTERFACES.md` (envelope, all types, topics, QoS/retain, Last Will, command
  JSON, config contract, platform-result file format), `docs/README.md` index + M0 stubs.
- **run_all**: includes `eval/results/platform/*.json|md` (Person B's results) with their
  bucket; buckets **Q** and **P** added.
- **Logs**: per-person logs/handoffs; root `WORK_LOG.md` / `HANDOFF_FOR_CLAUDE.md` are now
  index files; their old content moved (git mv) to `logs/WORK_LOG_HISTORY.md` and
  `handoff/HANDOFF_HISTORY_2026-09-24.md`. `CLAUDE.md` log references updated.

### Tests
- `tests/test_protocol.py` parses every ```text example in PROTOCOL.md (and round-trips it)
  and checks every ```invalid example is rejected for the stated reason.
- `tests/test_interfaces_doc.py` validates every JSON example in INTERFACES.md and its topic.
- `tests/test_contracts_v2.py` schema v2, bus (fake paho client), config, secrets, platform hook.
- `tests/test_no_secrets.py` fails if `secrets.yaml`/`.env` or a token-like string is tracked or staged.

```
cd storemind
.venv/Scripts/python -m pytest tests -q      # 212 passed (was 124; all old tests unchanged and passing)
```

### Real-broker smoke test (not part of the suite: needs a broker)
Throwaway `amqtt` broker in a scratch venv, two `MqttBus` instances (bridge node + vision node,
`listen=True`) and a raw watcher: the vision process received the bridge's `BEAM_CROSS` exactly
once; closing the bridge's socket without DISCONNECT made the broker publish the retained Will
`offline` on `storemind/smoke/stm32-01/status`. Mosquitto itself not tested yet (not installed).
