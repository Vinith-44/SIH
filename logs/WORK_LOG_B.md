# WORK_LOG — Person B (platform & hardware integration)

Append-only. Only Person B writes here. Dated entries; every number comes with the
command that produced it. Measured platform results go to
`storemind/storemind/eval/results/platform/` (format: `docs/INTERFACES.md` §5).

---

## 2026-09-25 — M5a: portable C protocol library + host tests (`b/m5a-protocol`)

Ram's session driven by Claude Code (Opus 5.5). Roadmap step 3.

- `firmware/stm32/protocol/`: C99 library (no HAL/heap/printf/float) implementing PROTOCOL.md
  text mode (encode, strict parse, typed uplink encoders, command decode with `$K` codes,
  byte-wise line assembler, seq-gap counter) plus CRC-16/CCITT-FALSE and COBS.
- `firmware/stm32/tools/gen_golden.py` turns every PROTOCOL.md example into
  `tests/golden_vectors.h`; `storemind/tests/test_firmware_golden.py` fails if it is stale.
- Python `sensors/protocol.py` gained `crc16_ccitt`, `cobs_encode`, `cobs_decode`
  (the C test compares against vectors they produce).
- CI: new `firmware (host C tests)` job: gcc and clang with `-Werror -Wconversion`, then ASan+UBSan.

Measured (commands run from the repo root):
- `python -m ziglang cc -std=c99 -Wall -Wextra -Werror -Wconversion -Wshadow -Wpedantic -Ifirmware/stm32/protocol/include -Ifirmware/stm32/protocol/tests firmware/stm32/protocol/src/sm_proto.c firmware/stm32/protocol/src/sm_cobs_crc.c firmware/stm32/protocol/tests/test_sm_proto.c -o t.exe && ./t.exe`
  → `989 checks, 0 failures (27 valid + 8 invalid doc examples, 8 binary vectors)`; same with `-fsanitize=undefined`.
- `arm-none-eabi-gcc` (xPack 15.2.1) `-mcpu=cortex-m3 -mthumb -Os` then `arm-none-eabi-size`:
  sm_proto.o text 4088 B, sm_cobs_crc.o text 724 B, data/bss 0.
- `ruff check .` clean; `pytest -q` (from storemind/) → 479 passed, 6 skipped.
- Not measured: the CMake/ctest path locally (no gcc on this laptop); CI runs it.

## 2026-09-25 — M5b: serial bridge + STM32 simulator (`b/m5b-bridge`)

Roadmap step 4. Docs: `docs/SENSOR_BRIDGE.md`.

- `sensors/bridge.py`: reader thread (pyserial or TCP), LineAssembler + protocol.py validation,
  $-line -> v2 events (WEIGHT, SHELF_MOTION, CAMERA_MOUNT, BEAM_CROSS, PRESENCE, ENVIRONMENT,
  NODE_HEALTH, `$R` -> SENSOR restock), crc/uart/lost counters, link-down watchdog (30 s) + ALERT,
  command worker with $K wait and 3 retries, $S time sync, ClockMapper (min-latency offset),
  reconnect with backoff, MQTT command listener (cmd/* -> ack), CLI.
- `sensors/simulator.py`: VirtualNode (periodic $H/$E/$W, 16 scenarios, command handling with
  PROTOCOL.md $K codes, noise injection), LoopbackTransport, TCP SimulatorServer, CLI.
- `health/systemd.py`: dependency-free sd_notify (READY / WATCHDOG), no-op off systemd.
- `tools/e2e_sensors.py` + CI step; `configs/sensors_sim.yaml`.
- Found and fixed while testing: bad-checksum lines were counted twice (crc_err and lost);
  a restarted node inflated `lost` by its seq restart (seq tracking now resets on reconnect/reboot).

Measured (bucket C, simulator):
- `pytest -q tests/test_bridge.py tests/test_simulator.py` → 42 passed. Includes 1 simulated hour
  over a clean link: 0 crc_err, 0 uart errors, 0 lost, 360 NODE_HEALTH; and a simulated 2-pack pick
  that Vinith's `ShelfInteractionEngine` turns into exactly one `PICKUP action=pick units=2`.
- `python tools/e2e_sensors.py` → 6/6 checks (every sensor event type in SQLite, LED command OK in
  1 attempt, reconnect after the simulator was killed, crc_err=0 rx_err=0 lost=0).
- CLI run: simulator `--speed 10` + `bridge --config configs/sensors_sim.yaml --no-mqtt --print --seconds 6`
  → 83 lines, 58 events, 0 errors, $S acked.
- `ruff check .` clean; `pytest -q` → 521 passed, 6 skipped.

## 2026-09-25 — M5c: STM32 firmware, 8 FreeRTOS tasks, CI build (`b/m5c-firmware`)

Roadmap step 5. Docs: `docs/FIRMWARE.md` §4, `docs/WIRING.md` (pin table).

- `firmware/stm32/`: CubeMX-layout project (Core/ + App/) built by CMake; ST HAL + CMSIS
  (STM32CubeF1 v1.8.7 submodules) and FreeRTOS 10.6.2 + ST CMSIS-RTOS2 fetched at configure
  time and pinned by SHA-256. 8 tasks with the guide's names and priority order; queues
  (sensor -> fusion -> UART), UART RX ring buffer from the USART1 IRQ, EXTI edges with DWT
  microsecond stamps, IWDG kicked only when every task has checked in, flash-backed config
  (tare / cal / MEMS thresholds / enabled sensors, CRC-16), reset cause in $H.
- `firmware/stm32/logic/`: portable patterns, two-beam direction, load-cell filter + stability,
  debounce, with host tests; its CMake also runs the protocol tests.
- CI: host job now builds logic+protocol; new `firmware (STM32F103 build)` job (apt gcc-arm-none-eabi,
  flash <= 60 KB / RAM <= 18 KB budget, .hex/.bin/.elf/.map artifact).

Measured:
- `python -m ziglang cc ... firmware/stm32/logic/tests/test_sm_logic.c` → `68 checks, 0 failures`.
- `cmake -S firmware/stm32 -B <build> -G Ninja -DCMAKE_TOOLCHAIN_FILE=<abs>/firmware/stm32/cmake/arm-none-eabi.cmake && cmake --build <build>`
  (xPack arm-none-eabi-gcc 15.2.1): 0 warnings; `arm-none-eabi-size`: text 28228, data 24, bss 12352
  → flash 28,252 B (44% of 63 KB), RAM 12,376 B (60% of 20 KB; 9 KB of it is the FreeRTOS heap).
- Not measured: anything on a real board (no Blue Pill here). Bring-up + HIL steps are M5d.

## 2026-09-25 — M5d: I2C1 mutex + bus recovery, HIL test (`b/m5d-i2c-hil`)

Roadmap step 6. Docs: FIRMWARE.md §5-6, HARDWARE_TODO.md "M5" (board bring-up, 8 steps).

- `App/Src/i2c_bus.c`: one FreeRTOS mutex for I2C1 (MEMS + Environment tasks); after a failed
  transfer or a BUSY flag stuck before a transfer: pins to GPIO, `sm_i2c_recover()` (9 SCL clocks +
  STOP), SWRST, re-init, one retry. `$H.i2c_err` counts failures + recoveries.
- `logic/`: `sm_i2c_recover()` portable, tested with a fake bus (released after 3 clocks, already free,
  SDA shorted -> gives up after 9, SCL shorted).
- `storemind/tools/hil_test.py`: heartbeat, every command's $K (incl. corrupted line -> ERR 1), RTT,
  sensor phase, framing soak; writes `eval/results/platform/hil_<label>.json`; `--simulate` / `--port`.
  Bridge keeps the last 64 $K in `bridge.acks` for it.

Measured:
- logic host tests (zig cc, -Werror -Wconversion): `75 checks, 0 failures`.
- firmware build: flash 29,240 B (45%), RAM 12,376 B (60%), 0 warnings.
- `python tools/hil_test.py --simulate --minutes 0.5 --rtt-rounds 20 --sensor-seconds 5 --no-write`
  → PASS; command RTT p50 27.3 ms, p95 42.1 ms over TCP to the simulator (bucket C: this is the
  laptop's loopback + Python, not the UART).
- Board: **not measured** (no Blue Pill here). Steps in HARDWARE_TODO.md "M5".

## 2026-09-25 — M6: MEMS firmware task + $M events + MEMS.md §1-3 (`b/m6-mems`)

Roadmap step 7.

- `firmware/stm32/logic/src/sm_mems.c`: portable, integer-only state machine IDLE -> CANDIDATE ->
  ACTIVE -> SETTLING with TOUCH / SETTLED / KNOCK (150 ms knock window) / TILT (boot reference,
  integer atan2, 2 s hold, re-arm on return); camera nodes report KNOCK/TILT only.
- `App/Src/mems_task.c`: two MPU6050s (0x68 shelf, 0x69 camera) at 100 Hz, +-2 g, DLPF 44 Hz;
  events -> fusion task -> `$M`; MEMS_THR from flash applied live; missing chip re-probed every 5 s.
- `storemind/tools/mems_test.py`: guided acceptance (30 picks, 10 put-backs, 10 touches, 10 bumps,
  10 camera knocks, 10 min idle) through the bridge + Vinith's ShelfInteractionEngine; `--simulate`.
- `docs/MEMS.md` §1-3 (chip/mounting, state machine + thresholds, calibration).

Measured (bucket C: logic on synthetic signals / simulator, not the sensor):
- `zig cc ... test_sm_mems.c` → `22 checks, 0 failures`; integer atan2 worst error 0.3 deg over 0-180 deg;
  10 min of +-25 mg noise → 0 events; 6.2 deg tilt → one TILT reading 5.5-6.9 deg.
- `python tools/mems_test.py --simulate --scale 0.2 --idle-minutes 10 --speed 100 --no-write` →
  picks 6/6, put-backs 2/2, touches 2/2 (0 counted as picks), bumps 2/2 (0 counted as picks),
  camera knocks 2/2, 0 false TOUCH in 10 simulated idle minutes. This exercises the tool and the
  fusion wiring; the simulator's $M lines come from its own model, not from sm_mems.c.
- firmware build: flash 31,468 B (49%), RAM 12,688 B (62%), 0 warnings.
- `pytest -q` → 523 passed, 6 skipped; `ruff check .` clean.
- Board: **not measured**. HARDWARE_TODO.md "M6" (Vinith's steps) + MEMS.md §3.

## 2026-09-25 — M7a: dashboard platform panels, alerts -> LED / buzzer (`b/m7a-dashboard`)

Roadmap step 8. Docs: `docs/DASHBOARD.md`.

- `api/panels.py` + `api/server.py`: camera-health, sensor-node, hardware, privacy and reorder panels from
  bus events (`/api/state.platform`, `/api/platform`, `/api/reorder`, `POST /api/node/{cmd}`); alerts from
  other processes (bridge SENSOR_LINK) join the alert list; acknowledging re-publishes the alert with ack=true.
- `alerts/tower.py`: one LED / buzzer policy for every alert key, incl. QUEUE_OVERFLOW (#12), SHELF_TILT,
  FALLEN_STOCK, CAMERA_MOVED/BUMP/TILT, AFTER_HOURS (#14) and SENSOR_LINK; worst open alert wins, ack turns
  it off. Used by TowerLightSink (in-process) and by the bridge service (ALERTs over MQTT). Voice lines added.
- `health/monitor.py`: Pi 5 PMIC power (corrected), mJ/frame, throttled-now in HEALTH (None off the Pi).
- Also committed: `eval/results/platform/hil_20260925_sim.json` from M5d's tool.

Measured:
- `python tools/hil_test.py --simulate --minutes 60 --speed 60 --rtt-rounds 50 --sensor-seconds 20 --label 20260925_sim`
  → PASS: 238,525 lines, 0 checksum / 0 framing / 0 lost, 0 reconnects; RTT p50 41.5 ms p95 43.6 ms
  (bucket C: simulator over TCP on the laptop, 60 MCU-hours at 60x).
- Live check in the browser (scratch launcher: pipeline live on the synthetic clips + simulator + bridge + API):
  all panels filled, no console errors, "LED alert" button -> $K OK, SHELF_TILT alert -> node LED+buzzer ALERT,
  bridge 146 lines / 0 errors.
- `pytest -q tests/test_platform_panels.py` → 16 passed; full suite below.
- `pytest -q` → 539 passed, 6 skipped; `ruff check .` clean.
