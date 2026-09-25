/*
 * drivers.h - thin hardware drivers for the sensor node (HAL underneath).
 * All I2C traffic goes through i2c_bus_* so errors are counted in one place.
 */
#ifndef DRIVERS_H
#define DRIVERS_H

#include <stdbool.h>
#include <stdint.h>

#include "stm32f1xx_hal.h"

extern I2C_HandleTypeDef hi2c1;
extern UART_HandleTypeDef huart1;
extern TIM_HandleTypeDef htim3;
extern IWDG_HandleTypeDef hiwdg;

/* ---- I2C1 bus ---------------------------------------------------------- */
void i2c_bus_init(void);
bool i2c_bus_read(uint8_t addr7, uint8_t reg, uint8_t *buf, uint16_t len);
bool i2c_bus_write(uint8_t addr7, uint8_t reg, uint8_t value);
bool i2c_bus_cmd(uint8_t addr7, uint8_t cmd);                 /* one command byte, no register */
bool i2c_bus_recv(uint8_t addr7, uint8_t *buf, uint16_t len); /* raw read, no register */

/* ---- HX711 ------------------------------------------------------------- */
typedef struct {
    GPIO_TypeDef *dout_port;
    uint16_t dout_pin;
    GPIO_TypeDef *sck_port;
    uint16_t sck_pin;
} hx711_t;

bool hx711_ready(const hx711_t *hx);
int32_t hx711_read(const hx711_t *hx);       /* 24-bit signed, channel A gain 128 */
void hx711_power_down(const hx711_t *hx);

/* ---- BH1750 light sensor (0x23) ---------------------------------------- */
bool bh1750_init(void);
bool bh1750_read_lux(int32_t *lux);

/* ---- BME280 (0x76): integer compensation from the Bosch datasheet ------ */
bool bme280_init(void);
/* temp in 0.1 degC, humidity in 0.1 %RH, pressure in 0.1 hPa (wire units) */
bool bme280_read(int32_t *temp_dc, int32_t *rh_dpct, int32_t *hpa_d);

#endif /* DRIVERS_H */
