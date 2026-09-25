/*
 * i2c_bus.c - I2C1 shared by the MEMS task and the Environment task.
 *
 * Two jobs (CLAUDE_CODE_PROMPT_V2 M5):
 *  1. A FreeRTOS mutex, so the MEMS and Environment tasks never interleave
 *     transfers on the one bus (MPU6050 0x68, BH1750 0x23, BME280 0x76).
 *  2. Bus recovery.  The STM32F1 I2C peripheral can lock up with BUSY stuck
 *     after a glitch (a slave left holding SDA low mid-byte; see the I2C section of
 *     ST errata sheet ES096 for the F103).  After a failed transfer, or when BUSY is set before we start, we
 *     take the pins back as GPIO, clock SCL up to 9 times until SDA is released,
 *     send a STOP (sm_i2c_recover, host-tested), reset the peripheral and re-init.
 *
 * $H i2c_err counts failed transfers plus recoveries, so a flaky cable shows up
 * on the dashboard long before it becomes a dead sensor.
 */
#include "app.h"
#include "board.h"
#include "drivers.h"

#define I2C_TIMEOUT_MS     10u
#define I2C_LOCK_WAIT_MS   50u

static SemaphoreHandle_t s_mutex;
volatile uint32_t g_i2c_recoveries;

void i2c_bus_init(void)
{
    if (s_mutex == NULL) {
        s_mutex = xSemaphoreCreateMutex();
    }
}

/* ---- recovery: pins as GPIO, driven by the portable sm_i2c_recover() ------ */

static void pin_scl(void *ctx, bool high)
{
    (void)ctx;
    HAL_GPIO_WritePin(I2C_SCL_PORT, I2C_SCL_PIN, high ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

static void pin_sda(void *ctx, bool high)
{
    (void)ctx;
    HAL_GPIO_WritePin(I2C_SDA_PORT, I2C_SDA_PIN, high ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

static bool read_sda(void *ctx)
{
    (void)ctx;
    return HAL_GPIO_ReadPin(I2C_SDA_PORT, I2C_SDA_PIN) == GPIO_PIN_SET;
}

static bool read_scl(void *ctx)
{
    (void)ctx;
    return HAL_GPIO_ReadPin(I2C_SCL_PORT, I2C_SCL_PIN) == GPIO_PIN_SET;
}

static void half_bit(void *ctx)
{
    (void)ctx;
    uint32_t start = DWT->CYCCNT;
    while ((DWT->CYCCNT - start) < SystemCoreClock / 200000u) {   /* 5 us */
    }
}

static void recover(void)
{
    GPIO_InitTypeDef g = {0};
    uint8_t clocks = 0;
    sm_i2c_pins_t pins = {pin_scl, pin_sda, read_sda, read_scl, half_bit, NULL};

    g_i2c_recoveries++;
    g_i2c_err++;
    HAL_I2C_DeInit(&hi2c1);
    g.Pin = I2C_SCL_PIN | I2C_SDA_PIN;
    g.Mode = GPIO_MODE_OUTPUT_OD;
    g.Pull = GPIO_PULLUP;
    g.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(I2C_SCL_PORT, &g);
    (void)sm_i2c_recover(&pins, &clocks);    /* stuck lines are still counted above */

    __HAL_RCC_I2C1_CLK_ENABLE();
    I2C1->CR1 |= I2C_CR1_SWRST;              /* clears a BUSY flag the pins alone cannot */
    I2C1->CR1 &= ~I2C_CR1_SWRST;
    HAL_I2C_Init(&hi2c1);                    /* MSP puts the pins back to AF open-drain */
}

static bool lock(void)
{
    if (xTaskGetSchedulerState() != taskSCHEDULER_RUNNING || s_mutex == NULL) {
        return true;                         /* before the scheduler: single-threaded */
    }
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(I2C_LOCK_WAIT_MS)) != pdTRUE) {
        g_i2c_err++;                         /* someone held the bus far too long */
        return false;
    }
    return true;
}

static void unlock(void)
{
    if (xTaskGetSchedulerState() == taskSCHEDULER_RUNNING && s_mutex != NULL) {
        xSemaphoreGive(s_mutex);
    }
}

/* Run one transfer with the lock held; recover and retry once if it fails. */
typedef HAL_StatusTypeDef (*xfer_fn)(uint8_t addr7, uint8_t reg, uint8_t *buf, uint16_t len);

static bool run(xfer_fn fn, uint8_t addr7, uint8_t reg, uint8_t *buf, uint16_t len)
{
    if (!lock()) {
        return false;
    }
    if (__HAL_I2C_GET_FLAG(&hi2c1, I2C_FLAG_BUSY)) {
        recover();                           /* BUSY before we even started: stuck bus */
    }
    bool ok = fn(addr7, reg, buf, len) == HAL_OK;
    if (!ok) {
        g_i2c_err++;
        recover();
        ok = fn(addr7, reg, buf, len) == HAL_OK;
        if (!ok) {
            g_i2c_err++;
        }
    }
    unlock();
    return ok;
}

static HAL_StatusTypeDef mem_read(uint8_t a, uint8_t r, uint8_t *b, uint16_t n)
{
    return HAL_I2C_Mem_Read(&hi2c1, (uint16_t)(a << 1), r, I2C_MEMADD_SIZE_8BIT, b, n, I2C_TIMEOUT_MS);
}

static HAL_StatusTypeDef mem_write(uint8_t a, uint8_t r, uint8_t *b, uint16_t n)
{
    return HAL_I2C_Mem_Write(&hi2c1, (uint16_t)(a << 1), r, I2C_MEMADD_SIZE_8BIT, b, n, I2C_TIMEOUT_MS);
}

static HAL_StatusTypeDef raw_write(uint8_t a, uint8_t r, uint8_t *b, uint16_t n)
{
    (void)b;
    (void)n;
    return HAL_I2C_Master_Transmit(&hi2c1, (uint16_t)(a << 1), &r, 1, I2C_TIMEOUT_MS);
}

static HAL_StatusTypeDef raw_read(uint8_t a, uint8_t r, uint8_t *b, uint16_t n)
{
    (void)r;
    return HAL_I2C_Master_Receive(&hi2c1, (uint16_t)(a << 1), b, n, I2C_TIMEOUT_MS);
}

bool i2c_bus_read(uint8_t addr7, uint8_t reg, uint8_t *buf, uint16_t len)
{
    return run(mem_read, addr7, reg, buf, len);
}

bool i2c_bus_write(uint8_t addr7, uint8_t reg, uint8_t value)
{
    return run(mem_write, addr7, reg, &value, 1);
}

bool i2c_bus_cmd(uint8_t addr7, uint8_t cmd)
{
    return run(raw_write, addr7, cmd, NULL, 0);
}

bool i2c_bus_recv(uint8_t addr7, uint8_t *buf, uint16_t len)
{
    return run(raw_read, addr7, 0, buf, len);
}
