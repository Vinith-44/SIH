# Troubleshooting

**Owner:** A + B · **Status:** platform side (Ram) filled in M11; add an entry every time something breaks in
testing. Format: symptom → likely cause → fix. Entries marked **(seen)** actually happened while building
StoreMind; the rest are the checks the code and the docs point to.

For shop staff the short version is docs/TEAM_GUIDE.md §4; this page is for whoever fixes it.

## 1. Dashboard and services

| symptom | likely cause | fix |
|---|---|---|
| dashboard does not load on a phone | phone on another Wi-Fi; `.local` names not resolved (some Android) | same Wi-Fi as the box; use the IP (`hostname -I` on the Pi) + `:8000` |
| dashboard does not load at all | pipeline not running, or port 8000 taken | `systemctl status storemind-pipeline`; `journalctl -u storemind-pipeline -n 50`; on a laptop add `--port 8001` |
| `storemind-pipeline` keeps restarting | it stopped sending watchdog pings (hung loop), a config error, or MemoryMax hit | `journalctl -u storemind-pipeline -b`: a config error names the exact key (`cameras.0.fps: ...`); "Watchdog timeout" = hang: note what the log said last and report it |
| pipeline unit stuck in "activating" then fails | `Type=notify` but this `run.py` has no systemd hook | update the code (`install_pi5.sh` again after `git pull`) |
| panels show old data after a config change | the service was not restarted | `sudo systemctl restart storemind-pipeline` |
| "Video stored" is not 0 | cannot happen (nothing opens a video writer) | report it as a bug; do not run the demo |

## 2. Sensor node (STM32) and the serial bridge

| symptom | likely cause | fix |
|---|---|---|
| sensor panel red, "link down", SENSOR_LINK alert | no `$H` heartbeat for 30 s: cable, power, or the node crashed | re-seat the UART/USB cable; the on-board LED must blink once a second. The bridge reopens the port with backoff (≤ 10 s) |
| on-board LED always on / always off | the firmware is stuck or not flashed | power-cycle; if it stays, reflash (docs/FIRMWARE.md §4). After a watchdog reset `$H` shows `reset_cause IWDG`: note it |
| no lines at all in a serial monitor | TX/RX not crossed, no common GND, wrong baud, or the port is the Pi's debug UART | PA9 → Pi pin 10 (RXD), PA10 → pin 8 (TXD), GND → pin 6; 115200 8N1; on a Pi 5 use `/dev/ttyAMA0` / `/dev/storemind-mcu`, **not** `/dev/serial0` (docs/SETUP_PI5.md §3) |
| garbage / kernel messages mixed into the lines on a Pi 5 | `enable_uart=1` in `config.txt` without a debug cable (the Pi then sends its console to GPIO14/15) | `sudo python3 scripts/pi5_boot_config.py` then reboot |
| `crc_err` climbing | electrical noise, long unshielded wires, a loose GND | shorter cable, common GND, twisted pair for TX/RX |
| `uart_err` / lost lines climbing | another program has the port open (serial monitor), or the USB-UART adapter drops bytes | close other programs; try another adapter; run `python tools/hil_test.py --port ... --minutes 10` |
| commands `TIMEOUT` (no `$K`) | the node's RX line is not connected, or another process owns the port | check PA10 ← Pi TXD; only one bridge may run (`systemctl stop storemind-bridge` before a manual one) |
| a command returns `ERR 4` | that sensor/actuator is disabled on the node (servo is off by default) | `cmd/CONFIG {"key":"ENABLE","args":["servo",1]}` or leave it off |
| a command returns `ERR 3` | bad argument (unknown slot / MEMS node / sensor name), or `MODE BIN` (not final yet) | use the ids in the config (`sensors.cell_map`, `sensors.mems_nodes`) |
| `i2c_err` climbing, `$E` fields empty | I2C wire loose, two sets of pull-ups, a sensor missing | re-seat SDA/SCL; one set of 4.7 kΩ pull-ups on the bus; the firmware recovers a stuck bus by itself and counts it |
| weight wrong / drifting | not tared, or never calibrated | empty shelf → Tare; known weight → CAL (docs/WIRING.md §3); both are stored in flash |
| weight never "stable" | the shelf is still being handled (MEMS active), or vibration | normal while someone touches it; otherwise raise the MEMS threshold (docs/MEMS.md §3) |
| MEMS node published as `shelf = m9` with a warning | the node id is not in `sensors.mems_nodes` | add it to the config with its shelf/slot or camera |
| **(seen)** a restarted node shows many "lost" lines | seq restarts at 0 after a reboot | fixed: the bridge resets its seq tracking on reconnect / reboot |
| `/dev/storemind-mcu` missing | udev rule not installed, or a USB-UART adapter instead of the header UART | `scripts/pi5_hardware.sh`; for USB adapters use the commented rules in `deploy/pi5/99-storemind-mcu.rules` |
| permission denied on the serial port | user not in `dialout` | `sudo usermod -a -G dialout <user>`, log in again (the service user is added by the installer) |

## 3. Firmware build and flashing

| symptom | likely cause | fix |
|---|---|---|
| **(seen)** `arm-none-eabi-gcc: fatal error: cannot execute 'as'` on Windows | the toolchain folder path is too long (Windows 260-character limit) | move the toolchain to a short folder, e.g. `C:\Users\<you>\tools\armgcc` |
| **(seen)** `Could not find toolchain file` | a relative `CMAKE_TOOLCHAIN_FILE` is resolved from the build folder | pass it as an absolute path (`$PWD/firmware/stm32/cmake/arm-none-eabi.cmake`) |
| **(seen)** `INCLUDE_vTaskDelayUntil and INCLUDE_xTaskDelayUntil are both defined` | FreeRTOS 10.6 renamed the option | only `INCLUDE_xTaskDelayUntil` in `FreeRTOSConfig.h` (done) |
| **(seen)** `#include CMSIS_device_header` error | ST's CMSIS-RTOS2 wrapper needs the device header name | defined in `firmware/stm32/CMakeLists.txt` (done) |
| configure fails downloading HAL / FreeRTOS | no internet, or a changed archive (hash mismatch) | configure once online; a hash mismatch means the download changed: do not just update the hash, check why |
| CI firmware job fails "flash/RAM budget" | the image grew past 60 KB flash / 18 KB RAM | look at `storemind_node.map`; move buffers to `static`, trim; the Black Pill (F411) is the fallback |
| ST-Link / OpenOCD refuses the chip ID | a clone chip (CKS32 / CS32) | add `-c "set CPUTAPID 0x2ba01477"` (docs/FIRMWARE.md §4) |
| flashed, but nothing on USART1 | BOOT0 left at 1 after the serial bootloader | BOOT0 = 0, reset |

## 4. Cameras

| symptom | likely cause | fix |
|---|---|---|
| a camera red / "stale" | the DVR/camera is off, the cable is out, or the stream URL is wrong | `python tools/probe.py <url>`; the reader reconnects with backoff by itself |
| a camera amber / "dark" | the scene is too dark or the IR mode is on | light; the shelf engine says UNKNOWN in the dark, never EMPTY (by design) |
| "Camera … moved or blocked" (CRITICAL) | the camera was knocked or covered; counting pauses | put it back; if the view changed, recalibrate (`tools/calibrate.py`) |
| measured FPS far below the stream's claim | the recorder claims 25, delivers 6; or it is the main stream | probe shows measured FPS; switch to the sub-stream (docs/CCTV_ONBOARDING.md step 5) |
| **(seen)** fake CCTV: "nothing listening on 127.0.0.1:8554" | a second MediaMTX is running (soak or another demo): the RTP ports clash | stop the other one; run one fake CCTV at a time |
| **(seen)** a second fake CCTV cannot start while go2rtc runs | go2rtc and MediaMTX both on 8554 | go2rtc is on 8564 (M2 design, `/etc/storemind/go2rtc.yaml`); MediaMTX keeps 8554 |
| people on a synthetic clip are not detected over RTSP | the synthetic clips are drawn scenes; their people exist only in `*_detections.json` | use the CAVIAR clips for anything live (docs/DEMO_RUNBOOK.md §3) |

## 5. Time, MQTT, disk

| symptom | likely cause | fix |
|---|---|---|
| events at the wrong time / dashboard clock wrong | no NTP and no RTC battery | `timedatectl`; `chronyc tracking`; fit the RTC battery (docs/SETUP_PI5.md §4) |
| DVR clock differs from the Pi | the DVR is not using the Pi as NTP server | DVR menu → NTP → the Pi's IP; `chronyc clients` shows it once it asks |
| services cannot connect to MQTT | Mosquitto down, or the password in `secrets.yaml` differs from the broker's | `systemctl status mosquitto`; re-run `install_pi5.sh` (it re-creates the broker password from `secrets.yaml`) |
| dashboard misses sensor events on the Pi | `mqtt.listen` is off in `store.yaml` | set `mqtt: {enabled: true, listen: true}` and restart the pipeline |
| database growing / disk filling | maintenance timer not running, or retention too long | `systemctl list-timers storemind-maintenance*`; run `python -m storemind.store.maintenance --config /etc/storemind/store.yaml --dry-run` |
| disk full | logs or the database | journald is capped at 200 MB; the pipeline drops events instead of crashing (chaos test); free space, then run maintenance |
