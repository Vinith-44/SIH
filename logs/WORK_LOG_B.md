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
