"""Edit the Pi 5 boot files for the STM32 link, idempotently (M8).

    sudo python3 scripts/pi5_boot_config.py            # apply
    python3 scripts/pi5_boot_config.py --dry-run       # show the diff only

Raspberry Pi 5 facts this relies on (raspberrypi.com/documentation/computers/configuration.html,
"Configure UARTs"):
  * the header pins GPIO14/15 (pins 8/10) are UART0 = /dev/ttyAMA0, disabled by default;
    enable with `dtoverlay=uart0-pi5`;
  * /dev/serial0 on a Pi 5 is the separate 3-pin debug header (UART10, /dev/ttyAMA10);
  * `enable_uart=1` with no cable on the debug header routes *kernel log output to GPIO14/15*,
    which would corrupt the protocol - so we make sure it is not set;
  * a `console=serial0,...` / `console=ttyAMA0,...` in cmdline.txt puts a login console on the
    port - removed.
Optional (--rtc-charge): `dtparam=rtc_bbat_vchg=3000000` charges the RTC backup battery; only for
the official rechargeable lithium-manganese cell (raspberry-pi.html, "Real Time Clock").

Pure functions + a thin CLI, so the edits are unit-tested (storemind/tests/test_pi5_setup.py).
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

CONFIG_TXT = Path("/boot/firmware/config.txt")
CMDLINE_TXT = Path("/boot/firmware/cmdline.txt")
MARK = "# StoreMind (scripts/pi5_boot_config.py)"


def edit_config_txt(text: str, rtc_charge: bool = False) -> str:
    """Ensure UART0 on the header, no enable_uart=1, optional RTC charging, under [all]."""
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"enable_uart\s*=\s*1", stripped):
            out.append(f"# {line}  {MARK}: routes kernel logs to GPIO14/15 on a Pi 5")
            continue
        out.append(line)
    wanted = ["dtoverlay=uart0-pi5"]
    if rtc_charge:
        wanted.append("dtparam=rtc_bbat_vchg=3000000")
    missing = [w for w in wanted if not any(ln.strip() == w for ln in out)]
    if missing:
        if out and out[-1].strip() != "":
            out.append("")
        out += ["[all]", MARK] + missing
    return "\n".join(out) + "\n"


def edit_cmdline_txt(text: str) -> str:
    """cmdline.txt is ONE line; drop serial consoles, keep everything else in order."""
    tokens = text.split()
    kept = [t for t in tokens if not re.fullmatch(r"console=(serial\d|ttyAMA\d+|ttyS\d),\d+", t)]
    return " ".join(kept) + "\n"


def _apply(path: Path, new: str, dry_run: bool) -> bool:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old == new:
        print(f"{path}: already correct")
        return False
    sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                               str(path), str(path) + " (new)"))
    if not dry_run:
        backup = path.with_name(f"{path.name}.storemind-{datetime.now():%Y%m%d%H%M%S}.bak")
        shutil.copy2(path, backup)
        path.write_text(new, encoding="utf-8")
        print(f"{path}: updated (backup {backup.name})")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rtc-charge", action="store_true", help="only with the rechargeable Li-Mn RTC cell")
    ap.add_argument("--config", type=Path, default=CONFIG_TXT)
    ap.add_argument("--cmdline", type=Path, default=CMDLINE_TXT)
    args = ap.parse_args(argv)
    changed = False
    for path, edit in ((args.config, lambda t: edit_config_txt(t, args.rtc_charge)),
                       (args.cmdline, edit_cmdline_txt)):
        if not path.exists():
            print(f"{path} not found: is this a Raspberry Pi OS Bookworm box?", file=sys.stderr)
            return 1
        changed |= _apply(path, edit(path.read_text(encoding="utf-8")), args.dry_run)
    if changed and not args.dry_run:
        print("REBOOT NEEDED: sudo reboot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
