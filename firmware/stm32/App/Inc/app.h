/*
 * app.h - what the 8 FreeRTOS tasks share.
 *
 * Data flow (the team guide's event-driven design):
 *
 *   sensor tasks --app_msg_t--> [event queue] --> Fusion/State task --\
 *   Health task ($H), UART task ($K) ------------------------------------> [tx queue] --> UART task --> Pi
 *   Pi --> UART RX ISR --> ring buffer --> UART task (commands) --> Actuator queue / config
 *
 * Only the UART task assigns `seq` and encodes lines, so the uplink seq is
 * always in transmit order.
 */
#ifndef APP_H
#define APP_H

#include <stdbool.h>
#include <stdint.h>

#include "FreeRTOS.h"
#include "cmsis_os2.h"
#include "queue.h"
#include "semphr.h"
#include "task.h"

#include "app_config.h"
#include "sm_logic.h"
#include "sm_proto.h"

/* One message to the Pi, before encoding (no seq yet). */
typedef struct {
    char type;                 /* 'W','M','B','D','P','E','R','H','K' */
    uint32_t ms;               /* MCU uptime when it happened */
    union {
        struct { uint8_t slot; int32_t grams; bool stable; } w;
        struct { uint8_t node; uint8_t ev; int32_t peak_mg, rms_mg, dur_ms, tilt_ddeg; } m;
        struct { uint8_t beam; bool clear; } b;
        struct { bool in; } d;
        struct { bool active; } p;
        struct { int32_t lux, temp_dc, rh_dpct, hpa_d; } e;
        struct { uint32_t uptime_s, free_heap, min_stack_words, i2c_err, uart_err; uint8_t cause; } h;
        struct { uint8_t cmd_seq; bool ok; uint8_t code; } k;
    } u;
} app_msg_t;

/* Actuator requests (from the UART task). */
typedef enum { ACT_LED = 0, ACT_BUZZER, ACT_SERVO } act_target_t;
typedef struct {
    uint8_t target;            /* act_target_t */
    uint8_t pattern;           /* sm_pattern_t */
    uint8_t angle;
} act_req_t;

/* Presence edges captured in EXTI interrupts. */
typedef enum { EDGE_BEAM_OUTER = 0, EDGE_BEAM_INNER, EDGE_PIR, EDGE_RESTOCK } edge_src_t;
typedef struct {
    uint8_t src;               /* edge_src_t */
    bool level;                /* raw pin level after the edge */
    uint32_t t_us;             /* DWT cycle counter / 72 */
} edge_t;

/* Tasks (names kept from the hardware team's FreeRTOS guide). */
enum {
    TASK_HX711 = 0, TASK_MEMS, TASK_PRESENCE, TASK_ENV, TASK_FUSION, TASK_UART,
    TASK_ACTUATOR, TASK_HEALTH, TASK_COUNT
};

extern osThreadId_t g_tasks[TASK_COUNT];
extern QueueHandle_t g_evq;        /* sensor tasks -> fusion */
extern QueueHandle_t g_txq;        /* -> UART task */
extern QueueHandle_t g_actq;       /* UART task -> actuator */
extern QueueHandle_t g_edgeq;      /* EXTI -> presence task */
extern SemaphoreHandle_t g_weight_mutex;

extern volatile uint32_t g_i2c_err;
extern volatile uint32_t g_uart_err;
extern volatile uint32_t g_alive;          /* one bit per task, cleared by the health task */
extern volatile bool g_shelf_active;       /* MEMS: shelf being handled -> weight unstable */
extern volatile uint64_t g_epoch_ms_at_sync;
extern volatile uint32_t g_tick_at_sync;
extern sm_reset_t g_reset_cause;
extern sm_weight_t g_weights[];

uint32_t app_ms(void);                       /* uptime in ms (FreeRTOS tick) */
uint32_t app_us(void);                       /* free-running us (DWT) */
void app_checkin(unsigned task);
bool app_post_event(const app_msg_t *msg);   /* sensor task -> fusion; false if full */
bool app_post_tx(const app_msg_t *msg);      /* straight to the UART task */

/* Task entry points */
void task_hx711(void *arg);
void task_mems(void *arg);
void task_presence(void *arg);
void task_env(void *arg);
void task_fusion(void *arg);
void task_uart(void *arg);
void task_actuator(void *arg);
void task_health(void *arg);

void app_start(void);                        /* create queues + tasks (called from main) */
void app_uart_rx_isr(uint8_t byte);          /* from USART1_IRQHandler */
bool app_rx_pop(uint8_t *byte);              /* UART task: next received byte */
extern volatile uint32_t g_rx_overflow;
uint32_t app_uart_errors(void);              /* MCU-side UART errors for $H */
void app_edge_isr(uint16_t pin);             /* from HAL_GPIO_EXTI_Callback */

#endif /* APP_H */
