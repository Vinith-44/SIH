# Serial protocol — STM32 sensor node ↔ Pi

**Status:** contract v1, agreed in PR-0 (research/26 §3.3). **Owner:** Person B (M5).
Changes go through a small contract PR approved by Person A.

This file is the single source of truth. `storemind/storemind/sensors/protocol.py`
implements it in pure Python, and `storemind/tests/test_protocol.py` parses
**every example line in this file**: the ```` ```text ```` blocks must decode and the
```` ```invalid ```` blocks must be rejected for the reason given after `#`. If you
change an example here, the test tells you whether the code still agrees.

Analogy: this is the track gauge. The firmware and the Pi are built from
opposite ends; they meet only if both follow this page.

## 1. Physical link

| | |
|---|---|
| MCU side | STM32F103C8 USART1, PA9 (TX) / PA10 (RX) |
| Pi side | Pi 5 header UART, GPIO14 / GPIO15 (pins 8 / 10), udev symlink `/dev/storemind-mcu` |
| Settings | 115200 baud, 8N1, no flow control, 3.3 V logic both sides, common GND |
| Dev laptop | USB-UART adapter (e.g. `COM5`), or the simulator over TCP (`sensors.tcp: host:port`) |

## 2. Demo (text) mode frame

```
$<T>,<seq>,<ms>,<field>,<field>...*<XX>\r\n
```

| Part | Meaning |
|---|---|
| `T` | One upper-case letter: the message type (§3). |
| `seq` | Rolling counter 0–255, **one counter per direction**. A gap means lines were lost; the Pi counts gaps into `NODE_HEALTH`. For Pi→MCU commands, `seq` is the command id that `$K` echoes back. |
| `ms` | Sender uptime in ms. MCU→Pi: the Pi maps it to wall-clock time with the last `$S` sync. Pi→MCU: Pi monotonic ms (the MCU only logs it). |
| fields | Comma-separated. **Every value is an integer** (scaled, see the tables), because newlib-nano `printf` has no float support. An **empty field means "not measured"** (e.g. `$E` with no BME280 fitted). Ids (`slot`, `node`, `door`, `zone`, `shelf`, `beam`) use `A–Z a–z 0–9 - _ .` only. |
| `XX` | XOR of every byte **between** `$` and `*`, as two **upper-case** hex digits. |
| Length | **At most 96 bytes** including `\r\n`. |

Receivers accept `\n` or `\r\n`, ignore bytes before `$`, and drop a line with no
terminator once it exceeds 96 bytes.

## 3. Messages

### MCU → Pi

| Line | Fields (in order) | Units / scale | Sent when | Becomes event |
|---|---|---|---|---|
| `$W` | `slot, grams, stable` | g (int); stable 0/1 | on change > 5 g, and every 10 s | `WEIGHT` |
| `$M` | `node, role, event, peak_mg, rms_mg, dur_ms, tilt_ddeg` | role `S` = shelf, `C` = camera mount; event `TOUCH`/`SETTLED`/`TILT`/`KNOCK`; mg; ms; tilt in **0.1°** (empty if not a tilt) | on each MEMS state-machine event (docs/MEMS.md) | role S → `SHELF_MOTION`, role C → `CAMERA_MOUNT` (TOUCH/SETTLED on a camera mount are dropped) |
| `$B` | `beam_id, state` | 0 = blocked, 1 = clear | every edge (raw, for debugging) | none (logged only) |
| `$D` | `door, direction` | `IN` / `OUT`, decided on the MCU from the two beams (µs timing) | each crossing | `BEAM_CROSS` |
| `$P` | `zone, active` | 0/1 | on change | `PRESENCE` |
| `$E` | `lux, temp_c×10, rh×10, hpa×10` | lux (int); 0.1 °C; 0.1 %RH; 0.1 hPa | every 5 s (BH1750 every 1 s allowed) | `ENVIRONMENT` |
| `$R` | `shelf` | — | restock button pressed (debounced) | triggers re-reference in the shelf engine |
| `$Q` | `n, x1, y1, v1, …` | n ≤ 3 targets; mm, mm, cm/s | 10 Hz while targets exist (optional LD2450, **disabled by default**) | fused into queue (M4) |
| `$H` | `uptime_s, free_heap, min_stack_words, i2c_err, uart_err, reset_cause` | s; bytes; words; counters; `POR`/`PIN`/`IWDG`/`WWDG`/`SW`/`BOR`/`LPWR`/`UNKNOWN` | every 10 s | `NODE_HEALTH` (the Pi adds `crc_err` and `link`) |
| `$K` | `cmd_seq, status, code` | `OK` / `ERR`; code 0 = ok, 1 = bad checksum, 2 = unknown command, 3 = bad argument, 4 = sensor disabled, 5 = busy | for **every** command received | command result on MQTT |

### Pi → MCU

| Line | Fields | Meaning |
|---|---|---|
| `$S` | `epoch_ms` | Time sync, every 60 s (`sensors.node.time_sync_s`) and at connect. |
| `$L` | `pattern` | LED: `OFF` / `ON` / `SLOW` / `FAST` / `ALERT` |
| `$Z` | `pattern` | Buzzer: same patterns. |
| `$V` | `angle` | Servo 0–180° (optional, demo only). |
| `$C` | `key, args…` | Config: `TARE,slot` · `CAL,slot,grams` · `MEMS_THR,node,mg` · `MODE,TXT\|BIN` · `ENABLE,sensor,0\|1` (sensor names as in `sensors.node.enabled_sensors`). Stored in flash where it makes sense (tare, cal, thresholds). |

**Retries:** the Pi resends a command up to **3 times** if no `$K` with the same
`cmd_seq` arrives within **200 ms** (`sensors.node.cmd_timeout_ms`, `cmd_retries`).
Commands are idempotent, so a duplicate is harmless.

## 4. Examples (parsed by the test — keep them valid)

Uplink, a shopper takes one 218 g pack from slot A1 (weight is unstable while
the shelf moves, then settles):

```text
$W,17,523040,A1,1840,1*31
$M,18,523610,m1,S,TOUCH,412,138,640,*1E
$W,19,523900,A1,1702,0*3A
$M,20,524980,m1,S,SETTLED,35,12,1370,*35
$W,21,525100,A1,1622,1*3D
```

The fusion engine ignores the unstable `$W` (seq 19) and computes Δ = −218 g
from the stable readings either side of the TOUCH → SETTLED window.

Other uplink lines:

```text
$M,21,530002,m1,S,TILT,90,40,2500,62*62
$M,22,601400,m2,C,KNOCK,1850,420,40,*02
$B,23,610001,b1,0*26
$D,24,610140,door1,IN*60
$P,25,611000,aisle1,1*23
$E,26,612000,420,284,615,10093*45
$E,27,672000,3,,,*70
$R,28,700000,shelf-a*4B
$Q,29,700100,2,-320,1450,0,410,2210,-12*74
$Q,30,700200,0*4B
$H,31,720000,720,6144,38,0,0,POR*3B
$K,32,720050,5,OK,0*67
$K,33,720300,6,ERR,3*21
```

`$E,27,…,3,,,` = lights nearly off (3 lux) on a node without a BME280.

Downlink:

```text
$S,5,3600100,1790239200000*4B
$L,6,3600250,ALERT*2A
$Z,7,3600260,SLOW*77
$V,8,3600300,90*7D
$C,9,3600400,TARE,A1*39
$C,10,3600500,CAL,A1,500*55
$C,11,3600600,MEMS_THR,m1,120*34
$C,12,3600700,MODE,BIN*34
$C,13,3600800,ENABLE,bme280,0*31
```

Invalid lines — the receiver must drop each one and count the reason:

```invalid
$W,40,800000,A1,1840,1*3C             # checksum
$W,40,800000,A1,1840,1*3b             # framing
W,40,800000,A1,1840,1*3B              # framing
$W,42,800200,A1,1840*26               # fields
$W,44,800400,A1,18.4,1*25             # fields
$M,41,800100,m1,S,SHAKE,412,138,640,*09   # fields
$V,43,800300,270*43                   # fields
$X,45,800500,1*49                     # unknown_type
```

## 5. Error handling (Pi side)

| Situation | Action | Counted in |
|---|---|---|
| Bad checksum / framing / fields / unknown type | drop the line | `NODE_HEALTH.crc_err` (checksum), `uart_err` (rest) |
| `seq` gap | accept the line, add the gap | `NODE_HEALTH` lost-line counter |
| No `$H` for 30 s | publish `NODE_HEALTH` with `link: down`, alert | — |
| USB-UART re-enumerates | reopen the port with backoff; never block the vision loop | reconnect count |

## 6. Production (binary) mode — outline, finalised by Person B in M5

Switched by `$C,MODE,BIN` (acknowledged with `$K` in text mode before the switch).
Same message set and field order as §3, packed little-endian into a struct:
`type (u8), seq (u8), ms (u32), payload…, crc (u16)`, CRC-16/CCITT-FALSE
(poly `0x1021`, init `0xFFFF`, no reflection, no final XOR) over everything before
the CRC, then **COBS**-encoded with a `0x00` delimiter. The per-type struct layout
is added here before any firmware or bridge code uses binary mode.

## Sources
- research/26 §3.3 (contract), research/23 §4.4 (why text + COBS/CRC), CLAUDE_CODE_PROMPT_V2 M5.
- COBS framing: https://www.embeddedrelated.com/showarticle/113.php
