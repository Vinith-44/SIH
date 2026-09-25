/*
 * board.h - Blue Pill (STM32F103C8T6) pin map for the StoreMind sensor node.
 * docs/WIRING.md has the same table with wiring notes; change both together.
 *
 * Rules behind the choices:
 *  - USART1 PA9/PA10 goes to the Pi (it is also the ROM serial bootloader port).
 *  - I2C1 PB6/PB7 carries MEMS + BH1750 + BME280.  I2C2 (PB10/PB11) is avoided
 *    because those pins are shared with USART3 on this board.
 *  - Every interrupt input has its own EXTI line number (0, 4, 5, 12-15): on the
 *    F1 an EXTI line is shared by all ports, so PA0 and PB0 cannot both be EXTI0.
 */
#ifndef BOARD_H
#define BOARD_H

#include "stm32f1xx_hal.h"

/* Pi 5 link: USART1, 115200 8N1 (docs/PROTOCOL.md section 1) */
#define PI_UART                 USART1
#define PI_UART_BAUD            115200u
#define PI_TX_PORT              GPIOA
#define PI_TX_PIN               GPIO_PIN_9
#define PI_RX_PORT              GPIOA
#define PI_RX_PIN               GPIO_PIN_10

/* LD2450 radar (optional, disabled by default): USART2 PA2/PA3 @ 256000 */
#define RADAR_UART              USART2

/* I2C1: MEMS (MPU6050 0x68 / ADXL345 0x53), BH1750 0x23, BME280 0x76 */
#define I2C_SCL_PORT            GPIOB
#define I2C_SCL_PIN             GPIO_PIN_6
#define I2C_SDA_PORT            GPIOB
#define I2C_SDA_PIN             GPIO_PIN_7
#define I2C_SPEED_HZ            100000u

/* HX711 load cells: DOUT on EXTI (falling edge = sample ready), PD_SCK output */
#define HX711_COUNT             2u
#define HX711_1_DOUT_PORT       GPIOA
#define HX711_1_DOUT_PIN        GPIO_PIN_0      /* EXTI0 */
#define HX711_1_SCK_PORT        GPIOA
#define HX711_1_SCK_PIN         GPIO_PIN_1
#define HX711_2_DOUT_PORT       GPIOA
#define HX711_2_DOUT_PIN        GPIO_PIN_4      /* EXTI4 */
#define HX711_2_SCK_PORT        GPIOA
#define HX711_2_SCK_PIN         GPIO_PIN_5

/* IR break-beams at the door (receiver output low = beam blocked) */
#define BEAM_OUTER_PORT         GPIOB
#define BEAM_OUTER_PIN          GPIO_PIN_12     /* EXTI12, street side */
#define BEAM_INNER_PORT         GPIOB
#define BEAM_INNER_PIN          GPIO_PIN_13     /* EXTI13 */

/* PIR (HC-SR501 output is 3.3 V: check before wiring, docs/WIRING.md) */
#define PIR_PORT                GPIOB
#define PIR_PIN                 GPIO_PIN_14     /* EXTI14 */

/* Restock button to GND, internal pull-up */
#define RESTOCK_PORT            GPIOB
#define RESTOCK_PIN             GPIO_PIN_15     /* EXTI15 */

/* MEMS interrupt (motion / data ready) */
#define MEMS_INT_PORT           GPIOB
#define MEMS_INT_PIN            GPIO_PIN_5      /* EXTI5 */

/* Outputs */
#define LED_ALERT_PORT          GPIOB
#define LED_ALERT_PIN           GPIO_PIN_8      /* tower LED via resistor / transistor */
#define BUZZER_PORT             GPIOB
#define BUZZER_PIN              GPIO_PIN_9      /* NPN transistor, active high */
#define LED_STATUS_PORT         GPIOC
#define LED_STATUS_PIN          GPIO_PIN_13     /* on-board LED, active LOW */
#define SERVO_PORT              GPIOA
#define SERVO_PIN               GPIO_PIN_6      /* TIM3_CH1, 50 Hz (optional) */

#endif /* BOARD_H */
