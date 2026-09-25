# Raspberry Pi 5 setup

**Owner:** Ram (Person B) · **Status:** M8: scripts and steps written and checked on the laptop
(`--dry-run`, unit tests); **not yet run on a Pi**. The first real run is `HARDWARE_TODO.md` "M8-deploy".

Analogy: the Pi is the shop's back office. This page takes it from a blank SD card to "cameras in,
sensor node in, dashboard out, clock right", and every step has a check.

## 1. SD card and first boot

1. Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit), Bookworm**. In the Imager settings: hostname
   `storemind`, a user, Wi-Fi (or use Ethernet: better for cameras), **enable SSH**, locale / timezone
   `Asia/Kolkata`.
2. Official 27 W USB-C supply and the active cooler (the benchmarks and soak assume both).
3. RTC backup battery (optional): the official **rechargeable** Li-Mn cell on the J5 "BAT" connector.
   Not a primary lithium cell, never a Li-ion cell (Raspberry Pi documentation, "Real Time Clock").
4. `ssh <user>@storemind.local`, then `sudo apt update && sudo apt full-upgrade -y && sudo reboot`.

## 2. Install StoreMind (one command)

```bash
git clone https://github.com/Vinith-44/SIH.git ~/SIH
```

```bash
cd ~/SIH && sudo ./scripts/install_pi5.sh --pi-hardware
```

```bash
sudo reboot
```

What it does is in `docs/OPERATIONS.md` §1 (packages, user, `/opt/storemind` + venv, go2rtc and MediaMTX,
`/etc/storemind` config with a generated MQTT password, Mosquitto, systemd units, maintenance timer).
`--pi-hardware` runs `scripts/pi5_hardware.sh`: sections 3–5 below. After the reboot:

```bash
~/SIH/scripts/pi5_check.sh
```

It prints PASS / FAIL for the board, UART, `/dev/storemind-mcu`, chrony, RTC, every service and the
dashboard.

## 3. Header UART to the STM32 (the Pi 5 is different)

From the Raspberry Pi documentation ("Configure UARTs",
https://www.raspberrypi.com/documentation/computers/configuration.html#configure-uarts):

| On a Pi 5 | Means for us |
|---|---|
| GPIO14 / GPIO15 (pins 8 / 10) are **UART0 = `/dev/ttyAMA0`**, disabled by default | enable it with `dtoverlay=uart0-pi5` in `/boot/firmware/config.txt` |
| `/dev/serial0` points to the separate 3-pin **debug** header (UART10, `/dev/ttyAMA10`) | never use `serial0` for the node |
| with `enable_uart=1` and no cable on the debug header, **kernel log output goes to GPIO14/15** | `enable_uart=1` must not be set: it would inject log text into our protocol |
| a `console=serial0,…` in `cmdline.txt` gives a login console on the port | removed |

`scripts/pi5_boot_config.py` makes exactly these edits (idempotent, keeps a `.bak`, `--dry-run` shows the
diff; unit-tested in `storemind/tests/test_pi5_setup.py`). Wiring: STM32 PA9 → Pi pin 10 (RXD),
PA10 → Pi pin 8 (TXD), GND → pin 6 (docs/WIRING.md). Both sides are 3.3 V: no level shifter.

**A stable name.** `deploy/pi5/99-storemind-mcu.rules` makes `/dev/storemind-mcu` → `/dev/ttyAMA0`, group
`dialout`, and tags it for systemd so `storemind-bridge` starts when the device exists and stops when it
goes. Using a USB-UART adapter instead? The same file has commented rules for CP2102 / CH340 / FT232.

Check by hand: `ls -l /dev/storemind-mcu`, then (services stopped)
`python -m storemind.sensors.bridge --config /etc/storemind/store.yaml --no-mqtt --print --seconds 30`
from `/opt/storemind/storemind` with the venv: `$H` / `$E` / `$W` lines become events.

## 4. Time: chrony, the RTC, the DVR and the STM32

Every timestamp has to agree: camera frames (via the DVR), the STM32 (via `$S`), and the Pi. A
queue timer or the IR-beam vs camera cross-check that compares the wrong seconds is quietly wrong.

- **chrony** (`deploy/pi5/chrony-storemind.conf` → `/etc/chrony/conf.d/`): syncs the Pi from the internet
  pool when online, **serves NTP to the shop LAN** (`allow` the private ranges), and keeps serving from its
  own clock when the internet is down (`local stratum 10 orphan`).
- **RTC**: the Pi 5 has a built-in RTC; with the backup battery the clock survives power cuts. Charging is
  off by default: `sudo ./scripts/pi5_hardware.sh --rtc-charge` adds `dtparam=rtc_bbat_vchg=3000000`
  (rechargeable cell only). Check: `cat /sys/class/rtc/rtc0/charging_voltage`.
- **DVR / NVR**: in its menu (System → Time / NTP), set the NTP server to the Pi's IP address and the
  interval to 60 min. Note the camera clock offset before and after in `logs/WORK_LOG_B.md`.
- **STM32**: the bridge sends `$S` (epoch ms) at connect and every `sensors.node.time_sync_s` (60 s); every
  uplink line carries MCU uptime, and the bridge maps it to wall time (docs/SENSOR_BRIDGE.md §3).

Check: `chronyc tracking` (Leap status Normal when online), `chronyc clients` (the DVR appears once it
asks), `timedatectl` (System clock synchronized, RTC time).

## 5. Services and dashboard

```bash
systemctl status 'storemind-*'
```

Dashboard: `http://storemind.local:8000/` from any phone on the shop Wi-Fi. Config:
`/etc/storemind/store.yaml` (cameras, zones, counters, shelves: `python tools/calibrate.py` on a snapshot;
DVR connection: docs/CCTV_ONBOARDING.md), then `sudo systemctl restart storemind-pipeline`.

## 6. After setup

`HARDWARE_TODO.md`: "M8-deploy" (this page for real + a 24 h soak on the Pi), "M5" (HIL on the board),
"M6" (MEMS shelf test), "M4" (canteen clip), "M8" (detector speed on the Pi).
