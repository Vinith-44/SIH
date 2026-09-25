#!/usr/bin/env bash
# Raspberry Pi 5 hardware setup for StoreMind (M8): header UART to the STM32, a stable
# /dev/storemind-mcu name, chrony as the shop's time server, optional RTC battery charging.
#
#   sudo ./scripts/pi5_hardware.sh                  # usually via: sudo ./scripts/install_pi5.sh --pi-hardware
#   ./scripts/pi5_hardware.sh --dry-run
#   sudo ./scripts/pi5_hardware.sh --rtc-charge     # ONLY with the official rechargeable RTC cell
#
# Then reboot, and check everything with: ./scripts/pi5_check.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
RTC_CHARGE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --rtc-charge) RTC_CHARGE=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
run() { if [ "$DRY_RUN" = 1 ]; then printf '    [dry-run] %s\n' "$*"; else "$@"; fi; }
say() { printf '\n==> %s\n' "$*"; }

if [ "$DRY_RUN" = 0 ] && [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0 $*" >&2
  exit 1
fi

say "boot files: UART0 on GPIO14/15, no serial console, no enable_uart=1"
BOOT_ARGS=()
[ "$DRY_RUN" = 1 ] && BOOT_ARGS+=(--dry-run)
[ "$RTC_CHARGE" = 1 ] && BOOT_ARGS+=(--rtc-charge)
if [ -f /boot/firmware/config.txt ]; then
  python3 "$REPO/scripts/pi5_boot_config.py" "${BOOT_ARGS[@]}"
else
  echo "    /boot/firmware/config.txt not found (not a Pi?): skipped"
fi
# The login console service on the port, if the image enabled one.
run systemctl disable --now serial-getty@ttyAMA0.service 2>/dev/null || true

say "udev: /dev/storemind-mcu"
run install -m 0644 "$REPO/deploy/pi5/99-storemind-mcu.rules" /etc/udev/rules.d/99-storemind-mcu.rules
run udevadm control --reload-rules
run udevadm trigger --subsystem-match=tty

say "chrony: serve time to the DVR / cameras on the LAN, keep serving offline"
run install -d /etc/chrony/conf.d
run install -m 0644 "$REPO/deploy/pi5/chrony-storemind.conf" /etc/chrony/conf.d/storemind.conf
# Only read if chrony.conf includes the conf.d folder; add the include if it does not.
if [ -f /etc/chrony/chrony.conf ] && ! grep -qE '^\s*(confdir|include)\s+/etc/chrony/conf\.d' /etc/chrony/chrony.conf; then
  if [ "$DRY_RUN" = 1 ]; then
    echo "    [dry-run] append 'confdir /etc/chrony/conf.d' to /etc/chrony/chrony.conf"
  else
    printf '\n# StoreMind\nconfdir /etc/chrony/conf.d\n' >> /etc/chrony/chrony.conf
  fi
fi
run systemctl enable chrony
run systemctl restart chrony

say "RTC"
if [ -e /dev/rtc0 ]; then
  echo "    /dev/rtc0 present"
else
  echo "    no /dev/rtc0 found: check again after the reboot (ls /dev/rtc*)"
fi
if [ "$RTC_CHARGE" = 1 ]; then
  echo "    battery charging enabled in config.txt (rtc_bbat_vchg=3000000) - rechargeable Li-Mn cell only"
fi

say "done: reboot, then ./scripts/pi5_check.sh"
