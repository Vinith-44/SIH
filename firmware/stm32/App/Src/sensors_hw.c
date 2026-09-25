/*
 * sensors_hw.c - HX711, BH1750 and BME280 drivers.
 *
 * HX711 (datasheet: https://cdn.sparkfun.com/datasheets/Sensors/ForceFlex/hx711_english.pdf):
 * DOUT goes low when a conversion is ready; 24 clock pulses shift the value out
 * MSB first, a 25th selects channel A / gain 128 for the next conversion.
 * PD_SCK must not stay high for more than 60 us or the chip powers down, so the
 * 25 pulses run inside a short critical section (~30 us at 72 MHz).
 *
 * BME280 compensation is the integer code from the Bosch datasheet (BST-BME280-DS002,
 * section 4.2.3 / 8.2), because the Cortex-M3 has no FPU.
 */
#include "app.h"
#include "drivers.h"

/* ------------------------------------------------------------------------- */
/* HX711                                                                      */
/* ------------------------------------------------------------------------- */

static inline void short_delay(void)
{
    for (volatile int i = 0; i < 8; i++) {
        __NOP();
    }
}

bool hx711_ready(const hx711_t *hx)
{
    return HAL_GPIO_ReadPin(hx->dout_port, hx->dout_pin) == GPIO_PIN_RESET;
}

int32_t hx711_read(const hx711_t *hx)
{
    uint32_t value = 0;
    taskENTER_CRITICAL();
    for (int i = 0; i < 24; i++) {
        HAL_GPIO_WritePin(hx->sck_port, hx->sck_pin, GPIO_PIN_SET);
        short_delay();
        value = (value << 1) | (HAL_GPIO_ReadPin(hx->dout_port, hx->dout_pin) == GPIO_PIN_SET ? 1u : 0u);
        HAL_GPIO_WritePin(hx->sck_port, hx->sck_pin, GPIO_PIN_RESET);
        short_delay();
    }
    HAL_GPIO_WritePin(hx->sck_port, hx->sck_pin, GPIO_PIN_SET);   /* 25th: channel A, gain 128 */
    short_delay();
    HAL_GPIO_WritePin(hx->sck_port, hx->sck_pin, GPIO_PIN_RESET);
    taskEXIT_CRITICAL();
    if (value & 0x800000u) {
        value |= 0xFF000000u;                                      /* sign-extend 24 -> 32 bits */
    }
    return (int32_t)value;
}

void hx711_power_down(const hx711_t *hx)
{
    HAL_GPIO_WritePin(hx->sck_port, hx->sck_pin, GPIO_PIN_SET);   /* > 60 us high = power down */
}

/* ------------------------------------------------------------------------- */
/* BH1750                                                                     */
/* ------------------------------------------------------------------------- */

#define BH1750_ADDR        0x23u
#define BH1750_POWER_ON    0x01u
#define BH1750_CONT_HRES   0x10u      /* 1 lx resolution, 120 ms */

bool bh1750_init(void)
{
    return i2c_bus_cmd(BH1750_ADDR, BH1750_POWER_ON) && i2c_bus_cmd(BH1750_ADDR, BH1750_CONT_HRES);
}

bool bh1750_read_lux(int32_t *lux)
{
    uint8_t b[2];
    if (!i2c_bus_recv(BH1750_ADDR, b, 2)) {
        return false;
    }
    uint32_t raw = ((uint32_t)b[0] << 8) | b[1];
    *lux = (int32_t)((raw * 5u) / 6u);        /* lux = raw / 1.2 */
    return true;
}

/* ------------------------------------------------------------------------- */
/* BME280                                                                     */
/* ------------------------------------------------------------------------- */

#define BME280_ADDR        0x76u
#define BME280_ID          0x60u

static struct {
    uint16_t T1; int16_t T2, T3;
    uint16_t P1; int16_t P2, P3, P4, P5, P6, P7, P8, P9;
    uint8_t H1; int16_t H2; uint8_t H3; int16_t H4, H5; int8_t H6;
    bool ok;
} cal;

static uint16_t u16le(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static int16_t s16le(const uint8_t *p) { return (int16_t)u16le(p); }

bool bme280_init(void)
{
    uint8_t id = 0;
    uint8_t a[26];
    uint8_t h[7];
    cal.ok = false;
    if (!i2c_bus_read(BME280_ADDR, 0xD0, &id, 1) || id != BME280_ID) {
        return false;
    }
    if (!i2c_bus_read(BME280_ADDR, 0x88, a, sizeof a) || !i2c_bus_read(BME280_ADDR, 0xE1, h, sizeof h)) {
        return false;
    }
    cal.T1 = u16le(&a[0]);  cal.T2 = s16le(&a[2]);  cal.T3 = s16le(&a[4]);
    cal.P1 = u16le(&a[6]);  cal.P2 = s16le(&a[8]);  cal.P3 = s16le(&a[10]);
    cal.P4 = s16le(&a[12]); cal.P5 = s16le(&a[14]); cal.P6 = s16le(&a[16]);
    cal.P7 = s16le(&a[18]); cal.P8 = s16le(&a[20]); cal.P9 = s16le(&a[22]);
    cal.H1 = a[25];
    cal.H2 = s16le(&h[0]);
    cal.H3 = h[2];
    cal.H4 = (int16_t)(((int16_t)(int8_t)h[3] * 16) | (h[4] & 0x0F));
    cal.H5 = (int16_t)(((int16_t)(int8_t)h[5] * 16) | (h[4] >> 4));
    cal.H6 = (int8_t)h[6];
    /* humidity x1, then temp x1 + pressure x1, sleep (forced mode per reading) */
    cal.ok = i2c_bus_write(BME280_ADDR, 0xF2, 0x01) && i2c_bus_write(BME280_ADDR, 0xF4, 0x24);
    return cal.ok;
}

bool bme280_read(int32_t *temp_dc, int32_t *rh_dpct, int32_t *hpa_d)
{
    uint8_t d[8];
    if (!cal.ok && !bme280_init()) {
        return false;
    }
    if (!i2c_bus_write(BME280_ADDR, 0xF4, 0x25)) {          /* forced mode: one measurement */
        cal.ok = false;
        return false;
    }
    osDelay(10);                                            /* ~8 ms at x1 oversampling */
    if (!i2c_bus_read(BME280_ADDR, 0xF7, d, sizeof d)) {
        cal.ok = false;
        return false;
    }
    int32_t adc_P = (int32_t)(((uint32_t)d[0] << 12) | ((uint32_t)d[1] << 4) | (d[2] >> 4));
    int32_t adc_T = (int32_t)(((uint32_t)d[3] << 12) | ((uint32_t)d[4] << 4) | (d[5] >> 4));
    int32_t adc_H = (int32_t)(((uint32_t)d[6] << 8) | d[7]);

    /* Temperature, 0.01 degC (datasheet 8.2) */
    int32_t var1 = ((((adc_T >> 3) - ((int32_t)cal.T1 << 1))) * ((int32_t)cal.T2)) >> 11;
    int32_t var2 = (((((adc_T >> 4) - ((int32_t)cal.T1)) * ((adc_T >> 4) - ((int32_t)cal.T1))) >> 12) *
                    ((int32_t)cal.T3)) >> 14;
    int32_t t_fine = var1 + var2;
    int32_t T = (t_fine * 5 + 128) >> 8;

    /* Pressure, Pa in Q24.8 */
    int64_t p1 = ((int64_t)t_fine) - 128000;
    int64_t p2 = p1 * p1 * (int64_t)cal.P6;
    p2 = p2 + ((p1 * (int64_t)cal.P5) << 17);
    p2 = p2 + (((int64_t)cal.P4) << 35);
    p1 = ((p1 * p1 * (int64_t)cal.P3) >> 8) + ((p1 * (int64_t)cal.P2) << 12);
    p1 = (((((int64_t)1) << 47) + p1)) * ((int64_t)cal.P1) >> 33;
    int64_t P = 0;
    if (p1 != 0) {
        P = 1048576 - adc_P;
        P = (((P << 31) - p2) * 3125) / p1;
        p1 = (((int64_t)cal.P9) * (P >> 13) * (P >> 13)) >> 25;
        p2 = (((int64_t)cal.P8) * P) >> 19;
        P = ((P + p1 + p2) >> 8) + (((int64_t)cal.P7) << 4);
    }

    /* Humidity, %RH in Q22.10 */
    int32_t v = t_fine - ((int32_t)76800);
    v = (((((adc_H << 14) - (((int32_t)cal.H4) << 20) - (((int32_t)cal.H5) * v)) + ((int32_t)16384)) >> 15) *
         (((((((v * ((int32_t)cal.H6)) >> 10) * (((v * ((int32_t)cal.H3)) >> 11) + ((int32_t)32768))) >> 10) +
            ((int32_t)2097152)) * ((int32_t)cal.H2) + 8192) >> 14));
    v = (v - (((((v >> 15) * (v >> 15)) >> 7) * ((int32_t)cal.H1)) >> 4));
    v = v < 0 ? 0 : v;
    v = v > 419430400 ? 419430400 : v;
    uint32_t H = (uint32_t)(v >> 12);

    *temp_dc = T / 10;                                  /* 0.01 C -> 0.1 C */
    *rh_dpct = (int32_t)((H * 10u) / 1024u);            /* Q22.10 %RH -> 0.1 % */
    *hpa_d = (int32_t)((P / 256) / 10);                 /* Pa -> 0.1 hPa */
    return true;
}
