# STM32 firmware

**Owner:** Ram (Person B) · **Status:** M5a protocol library · M5c firmware (builds in CI; **not yet run on a board**).

Analogy: the protocol library is the "phrasebook" both ends carry. The firmware
(STM32) and the bridge (Pi) are written separately, but they use the same
phrasebook, and CI checks that both phrasebooks match `docs/PROTOCOL.md`.

## 1. Layout

```
firmware/stm32/
  CMakeLists.txt       the firmware (arm-none-eabi); fetches HAL/CMSIS/FreeRTOS pinned by SHA-256
  cmake/               toolchain file + dependency list
  Core/                CubeMX-style start-up: main.c (clocks, pins, MX_*_Init), interrupts,
                       HAL MSP, board.h (pin map), FreeRTOSConfig.h, stm32f1xx_hal_conf.h
  App/                 the 8 tasks, drivers (HX711, BH1750, BME280, I2C), node_config (flash)
  STM32F103C8Tx_FLASH.ld  63 KB code + 1 KB config page, 20 KB RAM
  logic/               portable decision logic (patterns, beam direction, load-cell filter,
                       debounce) + host tests; its CMakeLists also builds the protocol tests
  protocol/            portable C99, no HAL, no heap, no printf, no floats
    include/sm_proto.h
    src/sm_proto.c     frame encode/parse, typed messages, command decode,
                       line assembler, seq-gap counter
    src/sm_cobs_crc.c  CRC-16/CCITT-FALSE + COBS (binary mode building blocks)
    tests/             host unit tests + golden_vectors.h (generated)
    CMakeLists.txt     host build: library + tests (+ optional sanitizers)
  tools/gen_golden.py  PROTOCOL.md examples -> tests/golden_vectors.h
```

## 2. Protocol library (M5a)

What it guarantees, and how it is tested:

| Guarantee | Test |
|---|---|
| Every ` ```text ` example in PROTOCOL.md decodes, and re-encodes to the same bytes | `test_golden_valid` (27 lines) |
| Every ` ```invalid ` example is rejected for the stated reason (`checksum`, `framing`, `fields`, `unknown_type`) | `test_golden_invalid` (8 lines) |
| The typed encoders (`sm_enc_weight`, `sm_enc_mems`, …) write exactly the doc's example lines | `test_typed_encoders_match_doc` |
| A line never exceeds 96 bytes; an invalid id or out-of-range value is refused whole, never sent half-built | `test_encoders_refuse_bad_input` |
| Commands (`$S $L $Z $V $C`) decode with the right `$K` code: 1 bad checksum (seq still read so the Pi can retry), 2 unknown command, 3 bad argument | `test_command_decode` |
| Byte-at-a-time line assembler ignores noise before `$`, drops runaway lines, restarts on a mid-line `$` | `test_line_assembler` |
| `seq` gaps are counted across the 255 → 0 wrap | `test_seq_tracker` |
| CRC-16 and COBS give the same bytes as the Python implementation (`sensors/protocol.py`) | `test_binary_building_blocks` |

Integer-only is enforced by the API: every value is `int32_t`/`uint32_t`/`uint64_t`,
an empty field is `SM_NONE` (`INT32_MIN`), and there is no `printf` (newlib-nano's has
no float support). Decoders read numeric fields as `int32`, so MCU counters in `$H`
must stay below 2^31.

**Keeping C and Python in step.** `golden_vectors.h` is generated from PROTOCOL.md
by `firmware/stm32/tools/gen_golden.py`; `storemind/tests/test_firmware_golden.py`
fails if the header is stale. So changing an example in PROTOCOL.md without
regenerating the header fails pytest, and a change that the C code disagrees with
fails the `firmware (host C tests)` CI job.

### Run the host tests

`firmware/stm32/logic` builds the protocol **and** logic tests (`firmware/stm32/protocol`
builds the protocol ones alone):

```bash
cmake -S firmware/stm32/logic -B build/host
cmake --build build/host
ctest --test-dir build/host --output-on-failure
```

With sanitizers: add `-DSM_SANITIZE=ON -DCMAKE_BUILD_TYPE=Debug` to the first line.
After editing PROTOCOL.md: `python firmware/stm32/tools/gen_golden.py`.

On Windows without gcc, `pip install ziglang` gives a C compiler:

```bash
python -m ziglang cc -std=c99 -Wall -Wextra -Werror -Wconversion -Ifirmware/stm32/protocol/include -Ifirmware/stm32/protocol/tests firmware/stm32/protocol/src/*.c firmware/stm32/protocol/tests/test_sm_proto.c -o test_sm_proto.exe
```

### Measured

- Protocol host tests: 989 checks, 0 failures (zig cc 0.16 / clang, `-Wconversion -Werror`,
  and again with `-fsanitize=undefined`). Logic host tests: see `logs/WORK_LOG_B.md`.
- Flash cost on Cortex-M3 (`arm-none-eabi-gcc` 15.2.1, `-Os`): `sm_proto.o` 4,088 B text,
  `sm_cobs_crc.o` 724 B, 0 B RAM. `--gc-sections` drops the decoders the MCU does not use.

## 3. Binary mode (not switched on yet)

CRC-16 and COBS are implemented and tested on both sides. The per-type struct
layout in PROTOCOL.md §6 is still an outline, so until a contract PR fixes it the
firmware answers `$C,MODE,BIN` with `$K,<seq>,ERR,3` and stays in text mode.

## 4. The firmware (M5c)

Hand-written in the STM32CubeMX layout (`Core/` + `MX_*_Init`), so it builds from a clean
clone with CMake only; no `.ioc` file is committed. ST's HAL and CMSIS (the STM32CubeF1 v1.8.7
submodules) and FreeRTOS 10.6.2 with ST's CMSIS-RTOS2 wrapper are downloaded at configure
time and checked against SHA-256 hashes (`cmake/deps.cmake`); nothing vendor-made is copied
into the repo.

### Build

Needs the Arm GNU toolchain, CMake 3.20+ and Ninja. Ubuntu / Pi:
`sudo apt install gcc-arm-none-eabi libnewlib-arm-none-eabi cmake ninja-build`.
Windows: the xPack `arm-none-eabi-gcc` zip, plus `pip install cmake ninja`. From the repo root:

```bash
cmake -S firmware/stm32 -B build/fw -G Ninja -DCMAKE_TOOLCHAIN_FILE="$PWD/firmware/stm32/cmake/arm-none-eabi.cmake"
```

```bash
cmake --build build/fw
```

Output: `build/fw/storemind_node.elf / .hex / .bin / .map` and a size report. If the toolchain
is not on `PATH`, set `ARM_TOOLCHAIN_DIR` to its `bin` folder first. On Windows keep the
toolchain in a short folder (e.g. `C:/Users/<you>/tools/armgcc`): gcc fails with
"cannot execute 'as'" when its internal paths pass Windows' 260-character limit.

CI (`firmware (STM32F103 build)`) builds the same way with Ubuntu's `gcc-arm-none-eabi`, fails the
PR if flash > 60 KB or RAM > 18 KB, and uploads the `.hex/.bin/.elf/.map` as an artifact.

### Flash

- **ST-Link (SWD: SWDIO, SWCLK, GND, 3.3 V):** `st-flash write build/fw/storemind_node.bin 0x8000000`,
  or `openocd -f interface/stlink.cfg -f target/stm32f1x.cfg -c "program build/fw/storemind_node.elf verify reset exit"`.
  **Clone chips (CKS32/CS32)** report a different core ID: add `-c "set CPUTAPID 0x2ba01477"`
  before `-f target/stm32f1x.cfg`.
- **Serial bootloader (no ST-Link):** BOOT0 = 1, reset, then
  `stm32flash -w build/fw/storemind_node.bin -v -g 0x0 /dev/ttyUSB0` over the same USART1 pins;
  BOOT0 back to 0 and reset.

### The 8 tasks (names from the hardware team's FreeRTOS guide)

| Task | Priority (CMSIS) | Stack | Does |
|---|---|---|---|
| Actuator | High (40) | 320 B | LED / buzzer patterns every 20 ms (`sm_pat_*`), servo PWM; the buzzer switches itself off after 5 s |
| UART | AboveNormal (32) | 640 B | owns USART1: encodes every uplink line with the one `seq` counter; assembles, validates and applies commands, answers each with `$K` |
| HX711 | Normal4 (28) | 384 B | DOUT interrupt → read 24 bits → `sm_weight_push` → `$W` on change > 5 g, flag change, or every 10 s |
| MEMS | Normal2 (26) | 512 B | two MPU6050s at 100 Hz → `sm_mems_sample` (docs/MEMS.md §2) → `$M` TOUCH / SETTLED / KNOCK / TILT; a missing chip is re-probed every 5 s |
| Presence | Normal2 (26) | 384 B | beam edges with µs timestamps → `sm_beam_edge` → `$B` / `$D`; PIR (200 ms debounce) → `$P`; restock button (50 ms, 1 s lockout) → `$R` |
| Fusion/State | Normal (24) | 384 B | sensor events → UART task; weight gating: while the shelf MEMS node is between TOUCH and SETTLED, `$W` goes out with `stable=0` |
| Environment | BelowNormal (16) | 512 B | BH1750 every 1 s; BME280 (forced mode, Bosch integer compensation) and `$E` every 5 s; a missing sensor = empty field |
| Health | Low (8) | 512 B | kicks the 4 s IWDG **only if every task checked in**; heartbeat LED; `$H` every 10 s with free heap, smallest stack high-water mark, I2C/UART error counters, reset cause |

Priorities follow the guide's model: actuation > UART/commands > HX711 > MEMS/presence > state
processing > environment > diagnostics. Interrupts (EXTI, USART1) run at NVIC priority 6, below
FreeRTOS's syscall ceiling (5), so they may use the `...FromISR` API.

Every sensor can be removed twice: at compile time (`App/Inc/app_config.h`, `SM_HAVE_x 0`) and at
run time (`$C,ENABLE,<sensor>,0`, stored in the flash config page). A compiled-out sensor answers
`ENABLE` with `$K` code 4. Tare, calibration, MEMS thresholds and the enabled set survive a reboot
(last flash page, CRC-16 protected).

### Memory budget (from the build, not measured on a board)

`arm-none-eabi-gcc` 15.2.1 (xPack), `-Os`, `arm-none-eabi-size`:

| | Bytes | Share |
|---|---|---|
| Flash (text + data) | 28,252 | 44% of the 63 KB code area |
| RAM (data + bss) | 12,376 | 60% of 20 KB |
| of which the FreeRTOS heap | 9,216 | task stacks, TCBs and queues come from here |

Every `$H` reports the free heap and the smallest stack high-water mark, so the first HIL run
(HARDWARE_TODO.md "M5") gives the real numbers. If RAM runs out as features grow, the fallback is
the STM32F411 "Black Pill" (128 KB RAM, same toolchain; change the device package, startup file and
linker script).

## 5. I2C1: one bus, one mutex, and recovery (M5d)

The MEMS task and the Environment task share I2C1 (MPU6050 0x68, BH1750 0x23, BME280 0x76).
`App/Src/i2c_bus.c` puts every transfer behind one FreeRTOS mutex (50 ms wait, then the attempt is
counted as an error instead of blocking a task).

The STM32F1 I2C peripheral can lock with BUSY stuck after a glitch, typically a slave left holding
SDA low in the middle of a byte (the F103 errata sheet ES096 has an I2C section on BUSY lock-ups;
research/26 §4.6 lists it as a Blue Pill gotcha). After any failed transfer, or when BUSY is already set before one starts:

1. de-init I2C1 and take PB6/PB7 as open-drain GPIO;
2. `sm_i2c_recover()` (portable, host-tested with a fake bus): clock SCL up to 9 times until the
   slave lets go of SDA, then send a STOP (NXP UM10204 §3.1.16);
3. pulse `SWRST` in `I2C1->CR1` (clears the BUSY flag the pins alone cannot) and re-init;
4. retry the transfer once.

`$H.i2c_err` counts failed transfers **and** recoveries, so a loose I2C cable shows on the dashboard
before it becomes a dead sensor. SCL shorted low or SDA still low after 9 clocks are reported as
failures (hardware faults) and the sensor stays "not measured" (empty `$E` fields) instead of hanging.

## 6. HIL test (M5d)

`storemind/tools/hil_test.py` talks to the board through the same `SensorBridge` the pipeline uses:
heartbeat, every command's `$K` code (including a deliberately corrupted line → `ERR 1`), command
round-trip time, which sensor lines arrive while you exercise them, and an N-minute framing soak.
It writes `storemind/storemind/eval/results/platform/hil_<label>.json` for RESULTS.md. `--simulate`
runs the same checks against the simulator (bucket C); `--port` runs them on the board (bucket B).
Steps: HARDWARE_TODO.md "M5".

## Still to come
Nothing: M6 is in MEMS.md §1–3.
