# Hardware inventory

**Owner:** team (Ram keeps it current) · **Status:** M11: the parts list the code and docs assume, with
**"in hand" left for the team to fill**. Nobody on the software side has counted the physical parts, so no quantity
here is a claim; an empty cell means "not confirmed". The code reads nothing from this page; people do.

How to fill it: count what is in the box, write the exact part / module name from the board or the invoice, and
put your name and the date in the last column. If a part differs from "assumed", say so in "notes": the firmware or
docs may need a change (e.g. an ADXL345 instead of an MPU6050 needs a driver, docs/MEMS.md §1).

## Sensor node (STM32)

| part | assumed by the code | needed | in hand | notes / checked by |
|---|---|---|---|---|
| STM32F103C8T6 "Blue Pill" | firmware target (64 KB flash, 20 KB RAM) | 1 (+1 spare) | | genuine or clone (CKS32/CS32)? matters for flashing |
| ST-Link V2 (or a USB-UART for the ROM bootloader) | docs/FIRMWARE.md §4 | 1 | | |
| USB-UART adapter, 3.3 V (CP2102 / CH340 / FT232) | laptop testing, HIL | 1 | | |
| load cell + HX711 board | 2 slots (`$W` channels "1", "2") | 2 each | | cell capacity (e.g. 5 kg)? |
| MEMS accelerometer **MPU6050 (GY-521)** | shelf node m1 (0x68) + camera node m2 (0x69) | 2 | | ADXL345 instead? needs a driver |
| IR break-beam pair (emitter + receiver) | door: outer + inner beam | 2 pairs | | receiver output 3.3 V? |
| PIR HC-SR501 | aisle presence, after-hours | 1 | | output 3.3 V (check) |
| BH1750 light sensor (0x23) | shelf lighting context | 1 | | |
| BME280 (0x76) | temperature / humidity / pressure | 1 | | BMP280 has no humidity: check the marking |
| buzzer + NPN transistor + 1 kΩ | alerts | 1 | | |
| LED (tower light) + resistor / transistor | alerts | 1 | | |
| push button (restock) | `$R` | 1 | | |
| servo SG90 (optional, off by default) | demo only | 0-1 | | needs its own 5 V supply |
| LD2450 radar (optional) | **not bought**, not compiled | 0 | | |
| TCA9548A I2C mux | only for > 2 MEMS nodes | 0 | | |
| 4.7 kΩ resistors (one I2C pull-up pair), jumper wires, breadboard / perfboard, JST leads | docs/WIRING.md | - | | |

## Edge box

| part | assumed by the code | needed | in hand | notes / checked by |
|---|---|---|---|---|
| Raspberry Pi 5 | the box (docs/SETUP_PI5.md) | 1 | | 4 GB or 8 GB? |
| official 27 W USB-C supply | benchmarks, soak | 1 | | |
| active cooler | benchmarks, soak | 1 | | |
| RTC battery (official rechargeable Li-Mn, J5) | clock through power cuts | 1 | | only this type may be charged |
| microSD 32 GB+ (A2) | OS + database | 1 (+1 spare) | | |
| Pi Camera Module 3 + ribbon | live `entrance` camera (`csi:0`) | 1 | | Pi 5 needs the 22-pin (mini) ribbon |
| USB webcam (optional) | `shelf-a` in `configs/demo_pi.yaml` | 0-1 | | |
| Ethernet cable / small switch | DVR link | 1 | | |

## Cameras and networks at the venue

| item | status | notes |
|---|---|---|
| college CCTV (DVR/NVR brand + model) | permission available; model **not yet known** | answers to docs/CCTV_ONBOARDING.md "Questions to ask college IT" go here |
| RTSP/ONVIF Wi-Fi camera (research/24 §9 demo) | not bought | |
| ESP32-S3 shelf cameras | pending team decision | software interface only |
| Qualcomm board (RUBIK Pi 3 / QCS6490) | **no** (AI Hub hosted devices instead) | deploy/qualcomm/README.md |
