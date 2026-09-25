#!/usr/bin/env bash
# Check a StoreMind Pi 5 after install + reboot (M8).  Read-only; prints PASS / FAIL / INFO.
#   ./scripts/pi5_check.sh
set -u
fail=0
pass() { printf 'PASS  %s\n' "$*"; }
bad()  { printf 'FAIL  %s\n' "$*"; fail=1; }
info() { printf 'INFO  %s\n' "$*"; }

model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo unknown)"
case "$model" in *"Raspberry Pi 5"*) pass "board: $model" ;; *) bad "board: $model (expected a Raspberry Pi 5)" ;; esac

grep -qx 'dtoverlay=uart0-pi5' /boot/firmware/config.txt 2>/dev/null \
  && pass "config.txt: dtoverlay=uart0-pi5" || bad "config.txt: dtoverlay=uart0-pi5 missing (scripts/pi5_boot_config.py)"
grep -qx 'enable_uart=1' /boot/firmware/config.txt 2>/dev/null \
  && bad "config.txt: enable_uart=1 (kernel logs would go to GPIO14/15)" || pass "config.txt: no enable_uart=1"
grep -qE 'console=(serial0|ttyAMA0)' /boot/firmware/cmdline.txt 2>/dev/null \
  && bad "cmdline.txt: serial console on the node's UART" || pass "cmdline.txt: no serial console"
[ -e /dev/ttyAMA0 ] && pass "/dev/ttyAMA0 exists" || bad "/dev/ttyAMA0 missing (reboot after the config change?)"
if [ -e /dev/storemind-mcu ]; then
  pass "/dev/storemind-mcu -> $(readlink -f /dev/storemind-mcu)"
else
  bad "/dev/storemind-mcu missing (udev rule deploy/pi5/99-storemind-mcu.rules)"
fi
id -nG storemind 2>/dev/null | grep -qw dialout && pass "user storemind in dialout" || bad "user storemind not in dialout"

if command -v chronyc >/dev/null; then
  chronyc -n tracking 2>/dev/null | grep -E 'Reference ID|Stratum|System time|Leap status' | sed 's/^/INFO  chrony /'
  chronyc -n tracking 2>/dev/null | grep -q 'Leap status *: Normal' && pass "chrony synchronised" \
    || info "chrony not synchronised yet (offline? it still serves from the RTC at stratum 10)"
else
  bad "chrony not installed"
fi
[ -e /dev/rtc0 ] && pass "RTC present (/dev/rtc0)" || bad "no /dev/rtc0"
if [ -r /sys/class/rtc/rtc0/charging_voltage ]; then
  info "RTC charging voltage: $(cat /sys/class/rtc/rtc0/charging_voltage) uV (0 = charging off)"
fi
timedatectl 2>/dev/null | grep -E 'System clock synchronized|RTC time' | sed 's/^ */INFO  /'

for svc in mosquitto storemind-go2rtc storemind-pipeline storemind-bridge storemind-maintenance.timer; do
  state="$(systemctl is-active "$svc" 2>/dev/null)"
  if [ "$state" = active ]; then pass "$svc active"; else bad "$svc $state"; fi
done
if command -v vcgencmd >/dev/null; then
  info "$(vcgencmd measure_temp) $(vcgencmd get_throttled)"
fi
curl -fsS -m 5 http://127.0.0.1:8000/api/health >/dev/null && pass "dashboard answers on :8000" \
  || bad "dashboard not answering on :8000"

[ "$fail" = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$fail"
