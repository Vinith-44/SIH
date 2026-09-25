/*
 * node_config.h - settings the Pi can change at run time, kept in the last
 * flash page (1 KB at 0x0800FC00) with a CRC-16, so they survive a reboot.
 */
#ifndef NODE_CONFIG_H
#define NODE_CONFIG_H

#include <stdbool.h>
#include <stdint.h>

/* Sensor bits: names exactly as in sensors.node.enabled_sensors (core/config.py). */
typedef enum {
    SENS_HX711 = 0, SENS_MEMS, SENS_IR_BEAM, SENS_PIR, SENS_BH1750, SENS_BME280,
    SENS_BUZZER, SENS_LED, SENS_SERVO, SENS_RESTOCK, SENS_LD2450, SENS_COUNT
} sensor_bit_t;

#define NODE_SLOTS      2u
#define NODE_MEMS_NODES 2u

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t enabled_mask;              /* bit = sensor_bit_t */
    int32_t hx_offset[NODE_SLOTS];      /* tare, raw counts */
    int32_t hx_num[NODE_SLOTS];         /* grams per count = num / den */
    int32_t hx_den[NODE_SLOTS];
    uint16_t mems_thr_mg[NODE_MEMS_NODES];
    uint16_t crc;                       /* CRC-16/CCITT over everything above */
} node_config_t;

extern node_config_t g_config;

void node_config_defaults(node_config_t *c);
bool node_config_load(void);            /* false -> defaults in use */
bool node_config_save(void);
int node_sensor_bit(const char *name);  /* -1 if unknown */
bool node_sensor_compiled(int bit);
bool node_enabled(int bit);             /* compiled AND enabled */
void node_set_enabled(int bit, bool on);

#endif /* NODE_CONFIG_H */
