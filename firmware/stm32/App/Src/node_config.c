/*
 * node_config.c - run-time settings in the last flash page (see node_config.h).
 */
#include "node_config.h"

#include <stddef.h>
#include <string.h>

#include "app_config.h"
#include "sm_proto.h"
#include "stm32f1xx_hal.h"

#define CONFIG_ADDR     0x0800FC00u     /* last 1 KB page of the 64 KB part (see linker script) */
#define CONFIG_MAGIC    0x534D4E43u     /* "SMNC" */
#define CONFIG_VERSION  1u

node_config_t g_config;

static const char *const SENSOR_NAMES[SENS_COUNT] = {
    "hx711", "mems", "ir_beam", "pir", "bh1750", "bme280", "buzzer", "led", "servo",
    "restock_button", "ld2450"
};

static const bool COMPILED[SENS_COUNT] = {
    SM_HAVE_HX711, SM_HAVE_MEMS, SM_HAVE_IR_BEAM, SM_HAVE_PIR, SM_HAVE_BH1750, SM_HAVE_BME280,
    SM_HAVE_BUZZER, SM_HAVE_LED, SM_HAVE_SERVO, SM_HAVE_RESTOCK, SM_HAVE_LD2450
};

static uint16_t config_crc(const node_config_t *c)
{
    return sm_crc16((const uint8_t *)c, offsetof(node_config_t, crc));
}

void node_config_defaults(node_config_t *c)
{
    memset(c, 0, sizeof *c);
    c->magic = CONFIG_MAGIC;
    c->version = CONFIG_VERSION;
    /* Everything compiled is on, except the demo-only servo and the radar
     * (same defaults as DEFAULT_SENSORS in storemind/core/config.py). */
    for (int i = 0; i < SENS_COUNT; i++) {
        if (COMPILED[i] && i != SENS_SERVO && i != SENS_LD2450) {
            c->enabled_mask = (uint16_t)(c->enabled_mask | (1u << i));
        }
    }
    for (unsigned s = 0; s < NODE_SLOTS; s++) {
        c->hx_offset[s] = 0;
        c->hx_num[s] = 1;
        c->hx_den[s] = 420;         /* ~420 counts/g for a 5 kg cell at gain 128: calibrate! */
    }
    c->mems_thr_mg[0] = 120;        /* shelf: a light touch */
    c->mems_thr_mg[1] = 600;        /* camera bracket: a real knock */
    c->crc = config_crc(c);
}

bool node_config_load(void)
{
    const node_config_t *stored = (const node_config_t *)CONFIG_ADDR;
    if (stored->magic == CONFIG_MAGIC && stored->version == CONFIG_VERSION &&
        stored->crc == config_crc(stored)) {
        memcpy(&g_config, stored, sizeof g_config);
        return true;
    }
    node_config_defaults(&g_config);
    return false;
}

bool node_config_save(void)
{
    g_config.crc = config_crc(&g_config);
    FLASH_EraseInitTypeDef erase = {
        .TypeErase = FLASH_TYPEERASE_PAGES, .PageAddress = CONFIG_ADDR, .NbPages = 1
    };
    uint32_t page_error = 0;
    bool ok = false;
    HAL_FLASH_Unlock();
    if (HAL_FLASHEx_Erase(&erase, &page_error) == HAL_OK) {
        const uint16_t *half = (const uint16_t *)(const void *)&g_config;
        ok = true;
        for (size_t i = 0; i < (sizeof g_config + 1u) / 2u; i++) {
            if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, CONFIG_ADDR + 2u * i, half[i]) != HAL_OK) {
                ok = false;
                break;
            }
        }
    }
    HAL_FLASH_Lock();
    return ok;
}

int node_sensor_bit(const char *name)
{
    for (int i = 0; i < SENS_COUNT; i++) {
        if (strcmp(name, SENSOR_NAMES[i]) == 0) {
            return i;
        }
    }
    return -1;
}

bool node_sensor_compiled(int bit)
{
    return bit >= 0 && bit < SENS_COUNT && COMPILED[bit];
}

bool node_enabled(int bit)
{
    return node_sensor_compiled(bit) && (g_config.enabled_mask & (1u << bit)) != 0u;
}

void node_set_enabled(int bit, bool on)
{
    if (!node_sensor_compiled(bit)) {
        return;
    }
    if (on) {
        g_config.enabled_mask = (uint16_t)(g_config.enabled_mask | (1u << bit));
    } else {
        g_config.enabled_mask = (uint16_t)(g_config.enabled_mask & ~(1u << bit));
    }
}
