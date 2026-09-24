/*
 * i2c_bus.c - I2C1 access for MEMS, BH1750 and BME280.
 * Every failure is counted in g_i2c_err (reported in $H).
 */
#include "app.h"
#include "drivers.h"

#define I2C_TIMEOUT_MS 10u

void i2c_bus_init(void)
{
}

static bool done(HAL_StatusTypeDef st)
{
    if (st != HAL_OK) {
        g_i2c_err++;
        return false;
    }
    return true;
}

bool i2c_bus_read(uint8_t addr7, uint8_t reg, uint8_t *buf, uint16_t len)
{
    return done(HAL_I2C_Mem_Read(&hi2c1, (uint16_t)(addr7 << 1), reg, I2C_MEMADD_SIZE_8BIT, buf, len,
                                 I2C_TIMEOUT_MS));
}

bool i2c_bus_write(uint8_t addr7, uint8_t reg, uint8_t value)
{
    return done(HAL_I2C_Mem_Write(&hi2c1, (uint16_t)(addr7 << 1), reg, I2C_MEMADD_SIZE_8BIT, &value, 1,
                                  I2C_TIMEOUT_MS));
}

bool i2c_bus_cmd(uint8_t addr7, uint8_t cmd)
{
    return done(HAL_I2C_Master_Transmit(&hi2c1, (uint16_t)(addr7 << 1), &cmd, 1, I2C_TIMEOUT_MS));
}

bool i2c_bus_recv(uint8_t addr7, uint8_t *buf, uint16_t len)
{
    return done(HAL_I2C_Master_Receive(&hi2c1, (uint16_t)(addr7 << 1), buf, len, I2C_TIMEOUT_MS));
}
