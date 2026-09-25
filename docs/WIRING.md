# Wiring

**Owner:** Ram (Person B) · **Status:** M5c pin map (same as `firmware/stm32/Core/Inc/board.h`;
change both together) · **Not yet checked on a real board** — follow `HARDWARE_TODO.md` "M5".

Analogy: the Blue Pill is a small switchboard. Every sensor gets its own labelled socket,
and the only cable to the Pi is one 3-wire serial line.

## 1. Pin table (STM32F103C8 "Blue Pill")

| Function | Blue Pill pin | Connects to | Notes |
|---|---|---|---|
| UART TX → Pi | **PA9** (USART1) | Pi 5 **GPIO15 / pin 10** (RXD) | 115200 8N1, 3.3 V both sides |
| UART RX ← Pi | **PA10** (USART1) | Pi 5 **GPIO14 / pin 8** (TXD) | also the ROM serial bootloader port |
| GND | GND | Pi 5 **pin 6** | **common ground is mandatory** |
| I2C1 SCL | **PB6** | MEMS, BH1750, BME280 SCL | 100 kHz; **one** set of 4.7 kΩ pull-ups on the whole bus |
| I2C1 SDA | **PB7** | MEMS, BH1750, BME280 SDA | addresses: MPU6050 0x68, BH1750 0x23, BME280 0x76 (no clash) |
| MEMS INT | **PB5** (EXTI5) | MPU6050 INT | motion / data-ready interrupt |
| HX711 #1 DOUT | **PA0** (EXTI0) | HX711 DT (slot "1") | falling edge = sample ready |
| HX711 #1 SCK | **PA1** | HX711 SCK | |
| HX711 #2 DOUT | **PA4** (EXTI4) | HX711 DT (slot "2") | |
| HX711 #2 SCK | **PA5** | HX711 SCK | |
| IR beam outer | **PB12** (EXTI12) | receiver OUT (street side) | low = blocked; internal pull-up |
| IR beam inner | **PB13** (EXTI13) | receiver OUT (shop side) | 10–20 cm from the outer beam, hip height |
| PIR | **PB14** (EXTI14) | HC-SR501 OUT | check the output is 3.3 V (it is on the HC-SR501) |
| Restock button | **PB15** (EXTI15) | button to GND | internal pull-up, debounced 50 ms |
| Tower LED | **PB8** | LED + 330 Ω (or NPN for a bright tower light) | |
| Buzzer | **PB9** | NPN transistor base via 1 kΩ | never drive a buzzer straight from a pin |
| Servo (optional) | **PA6** (TIM3_CH1) | servo signal | servo on its **own 5 V supply**, grounds joined |
| Status LED | PC13 | on-board LED | blinks once a second = health task alive |
| SWD | PA13 / PA14 | ST-Link SWDIO / SWCLK | for flashing (FIRMWARE.md) |
| LD2450 radar | PA2 / PA3 (USART2) | radar RX / TX | optional, not purchased, not compiled |

Every interrupt input uses a different EXTI number (0, 4, 5, 12, 13, 14, 15): on the F103 an
EXTI line is shared by the same pin number on every port, so PA0 and PB0 cannot both interrupt.
I2C2 (PB10/PB11) is avoided because those pins are also USART3 on this board.

## 2. Power

| Rail | From | Feeds |
|---|---|---|
| 5 V | Pi 5 pin 2/4, or a USB supply | Blue Pill 5 V pin, HX711 VCC (or 3.3 V), PIR, IR beam emitters |
| 3.3 V | Blue Pill regulator | MEMS, BH1750, BME280, IR receivers (if 3.3 V parts) |
| 5 V (separate) | its own supply | servo (it pulls >500 mA spikes that reset the MCU) |

Signals into the Blue Pill must be 3.3 V. PA9/PA10 and most PB pins are 5 V tolerant (FT),
PA0-PA7 are **not** — keep the HX711 at 3.3 V logic, or power it from 3.3 V.

## 3. Load cells

Each load cell (4 wires: E+ red, E- black, A+ white/green, A- green/white) goes to one HX711
(channel A, gain 128). Mount the cell under the slot with the arrow pointing down. After wiring:
`$C,TARE,1` with the slot empty, then `$C,CAL,1,500` with a known 500 g weight (the bridge can
send both: `cmd/TARE` and `cmd/CAL` on MQTT, docs/INTERFACES.md §3.4).

## 4. Pi 5 side

Header UART on GPIO14/15, enabled in `/boot/firmware/config.txt`, serial console off, symlink
`/dev/storemind-mcu` (docs/SETUP_PI5.md, `deploy/pi5/99-storemind-mcu.rules`).
