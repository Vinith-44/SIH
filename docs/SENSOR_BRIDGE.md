# Sensor bridge and STM32 simulator

**Owner:** Ram (Person B) · **Status:** M5b · Code: `storemind/storemind/sensors/{bridge,simulator}.py`

Analogy: the bridge is the sensor node's interpreter. The STM32 speaks short serial
lines ([PROTOCOL.md](PROTOCOL.md)); StoreMind speaks events ([INTERFACES.md](INTERFACES.md)).
The simulator is a stand-in node, so everything can be tested before the board is wired.

## 1. Run it on the laptop (no hardware)

Two terminals, from `storemind/`:

```bash
python -m storemind.sensors.simulator --port 7777 --scenario demo
```

```bash
python -m storemind.sensors.bridge --config configs/sensors_sim.yaml --no-mqtt --print
```

The bridge prints every event as a JSON line and, when stopped, its counters.
One-shot end-to-end check (simulator → bridge → SQLite, reconnect, command ack):

```bash
python tools/e2e_sensors.py
```

Simulator options: `--speed 10` (10 MCU seconds per real second), `--scenario pick|put_back|lean|
trolley_knock|shelf_tilt|camera_knock|camera_tilt|rush|empty_shelf|restock|lights_off|after_hours|
i2c_fault|reboot|demo`, `--once`, `--disable bme280` (sensor not fitted, repeatable),
`--bad-checksum 0.02 --drop 0.02 --garbage 0.02` (a noisy cable).

## 2. What each line becomes

| Serial | Event | Notes |
|---|---|---|
| `$W` | `WEIGHT` | `slot` is the load-cell channel; `sensors.cell_map` maps it to a shelf slot |
| `$M` role `S` | `SHELF_MOTION` | shelf/slot from `sensors.mems_nodes`; the wire tilt is not in the payload |
| `$M` role `C` | `CAMERA_MOUNT` (`cam` set on the envelope too) | TOUCH/SETTLED on a camera mount are dropped |
| `$D` | `BEAM_CROSS` | `IN/OUT` → `in/out`; `t_ms_mcu` = MCU ms |
| `$P` | `PRESENCE` | wakes cameras (M2 `ingest/pir_wake.py`) and the after-hours rule |
| `$E` | `ENVIRONMENT` | ×10 fields scaled back; empty = not fitted |
| `$R` | `SENSOR` `sensor: restock`, `channel: <shelf>` | the shelf engine re-references (INTERFACES.md M3) |
| `$H` | `NODE_HEALTH` | `uart_err` = MCU's + Pi-side bad lines + lost lines; `crc_err` = Pi-side bad checksums |
| `$K` | — | completes the waiting command |
| `$B` | — | raw beam edges, debug log only |
| `$Q` | `SENSOR` `sensor: ld2450` | only when `ld2450` is enabled (off by default) |

A MEMS node id that is not in `sensors.mems_nodes` is still published (shelf or cam =
the node id) with a warning, so a wiring mistake shows up on the dashboard instead
of vanishing.

## 3. Reliability rules (PROTOCOL.md §5)

| Situation | What the bridge does |
|---|---|
| Bad checksum | drop, `crc_err` +1 |
| Framing / fields / unknown type / downlink line on the uplink | drop, `uart_err` +1 |
| `seq` gap | accept, count the lines that never arrived (a line that arrived broken is not also counted as lost) |
| MCU reboot (ms goes backwards) or reconnect | seq tracking and the clock offset restart |
| No `$H` for 30 s | `NODE_HEALTH link: down` + one WARN alert `SENSOR_LINK:<node>`; back to `up` on the next `$H` |
| Port disappears (USB re-enumerates) | reopen with backoff 0.5 s → 10 s, `reconnects` +1 |
| Command | resend the same line (same `cmd_seq`) up to `cmd_retries` times after `cmd_timeout_ms`; arguments are validated before sending |

**Time.** Each line carries MCU uptime ms. `ClockMapper` uses the smallest observed
"receive time − ms" as the offset (serial latency only ever makes a line look late),
so event timestamps are not shifted by UART or USB buffering. `$S` time sync goes out at
connect and every `time_sync_s`.

**Never blocks the vision loop.** The pipeline and alert manager only call
`set_led`, `set_buzzer`, `set_light`, `buzz`, `tare`, `calibrate`; each queues the
command for the bridge's worker thread.

## 4. On the Pi (separate process, MQTT)

With `mqtt.enabled: true` the bridge publishes to Mosquitto and listens on
`storemind/{store}/{node}/cmd/{LED|BUZZER|SERVO|TARE|CAL|CONFIG}`, answering on
`…/ack` (INTERFACES.md §3.4). The systemd unit is in `deploy/pi5/` (M7b).

## 5. Measured (bucket C — simulator, not hardware)

| Check | Result | Command |
|---|---|---|
| 1 simulated hour, clean link: framing/checksum errors, lost lines | 0 / 0 / 0; 360 heartbeats | `pytest storemind/tests/test_bridge.py::test_one_simulated_hour_over_a_clean_link_has_zero_framing_errors` |
| Noisy link (2% bad checksum, 2% dropped, 2% garbage), 10 simulated min | every corrupted line counted as `crc_err`; drops counted as `lost` | `pytest storemind/tests/test_bridge.py::test_noisy_link_is_counted_not_crashed` |
| Simulated pick of 2 packs → Vinith's M6 engine | 1 `PICKUP action=pick units=2` | `pytest storemind/tests/test_simulator.py::test_simulated_pick_is_counted_by_vinith_fusion_engine` |
| e2e over TCP (simulator → bridge → SQLite, kill + restart, LED command) | 6/6 checks | `python tools/e2e_sensors.py` |

The real-board numbers (HIL framing test, command round-trip time) come from
`storemind/tools/hil_test.py` (M5d) and are **not measured yet**.
