# STM32 firmware

**Owner:** Ram (Person B) · **Status:** M5a (portable protocol library + host tests).

Analogy: the protocol library is the "phrasebook" both ends carry. The firmware
(STM32) and the bridge (Pi) are written separately, but they use the same
phrasebook, and CI checks that both phrasebooks match `docs/PROTOCOL.md`.

## 1. Layout

```
firmware/stm32/
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

### Run the tests

```bash
cmake -S firmware/stm32/protocol -B build/proto
cmake --build build/proto
ctest --test-dir build/proto --output-on-failure
```

With sanitizers: add `-DSM_SANITIZE=ON -DCMAKE_BUILD_TYPE=Debug` to the first line.
After editing PROTOCOL.md: `python firmware/stm32/tools/gen_golden.py`.

On Windows without gcc, `pip install ziglang` gives a C compiler:

```bash
python -m ziglang cc -std=c99 -Wall -Wextra -Werror -Wconversion -Ifirmware/stm32/protocol/include -Ifirmware/stm32/protocol/tests firmware/stm32/protocol/src/*.c firmware/stm32/protocol/tests/test_sm_proto.c -o test_sm_proto.exe
```

### Measured

- Host tests: 989 checks, 0 failures (zig cc 0.16 / clang, `-Wconversion -Werror`, and again
  with `-fsanitize=undefined`). Command in `logs/WORK_LOG_B.md`.
- Flash cost on Cortex-M3 (`arm-none-eabi-gcc` 15.2.1, `-Os`): `sm_proto.o` 4,088 B text,
  `sm_cobs_crc.o` 724 B, 0 B RAM. `--gc-sections` drops the decoders the MCU does not use.

## 3. Binary mode (not switched on yet)

CRC-16 and COBS are implemented and tested on both sides. The per-type struct
layout in PROTOCOL.md §6 is still an outline, so until a contract PR fixes it the
firmware answers `$C,MODE,BIN` with `$K,<seq>,ERR,3` and stays in text mode.

## Still to come in this file
Build/flash (M5c), the 8 FreeRTOS tasks, RAM budget and stack high-water marks,
I2C recovery (M5d), MEMS state machine (M6).
