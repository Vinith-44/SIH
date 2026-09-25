/*
 * app.c - queues, the 8 tasks, and the interrupt glue.
 *
 * Priorities follow the team guide's model (highest first):
 *   Actuation > UART RX/commands > HX711 > MEMS / presence > state processing
 *   > environment > diagnostics.
 */
#include "app.h"

#include <string.h>

#include "board.h"
#include "drivers.h"
#include "node_config.h"

osThreadId_t g_tasks[TASK_COUNT];
QueueHandle_t g_evq;
QueueHandle_t g_txq;
QueueHandle_t g_actq;
QueueHandle_t g_edgeq;
SemaphoreHandle_t g_weight_mutex;

volatile uint32_t g_i2c_err;
volatile uint32_t g_uart_err;
volatile uint32_t g_alive;
volatile bool g_shelf_active;
volatile uint64_t g_epoch_ms_at_sync;
volatile uint32_t g_tick_at_sync;
sm_reset_t g_reset_cause = SM_RESET_UNKNOWN;
sm_weight_t g_weights[NODE_SLOTS];

/* UART RX ring buffer: filled by the USART1 interrupt, drained by the UART task. */
#define RX_RING 256u
static volatile uint8_t s_rx[RX_RING];
static volatile uint16_t s_rx_head;
static volatile uint16_t s_rx_tail;
volatile uint32_t g_rx_overflow;

typedef struct {
    const char *name;
    osThreadFunc_t fn;
    uint32_t stack_bytes;
    osPriority_t prio;
} task_def_t;

static const task_def_t TASKS[TASK_COUNT] = {
    [TASK_HX711]    = {"hx711",    task_hx711,    384, osPriorityNormal4},
    [TASK_MEMS]     = {"mems",     task_mems,     512, osPriorityNormal2},
    [TASK_PRESENCE] = {"presence", task_presence, 384, osPriorityNormal2},
    [TASK_ENV]      = {"env",      task_env,      512, osPriorityBelowNormal},
    [TASK_FUSION]   = {"fusion",   task_fusion,   384, osPriorityNormal},
    [TASK_UART]     = {"uart",     task_uart,     640, osPriorityAboveNormal},
    [TASK_ACTUATOR] = {"actuator", task_actuator, 320, osPriorityHigh},
    [TASK_HEALTH]   = {"health",   task_health,   512, osPriorityLow},
};

uint32_t app_ms(void)
{
    return (uint32_t)xTaskGetTickCount();          /* 1 kHz tick */
}

uint32_t app_us(void)
{
    return DWT->CYCCNT / (SystemCoreClock / 1000000u);
}

void app_checkin(unsigned task)
{
    taskENTER_CRITICAL();
    g_alive |= 1u << task;
    taskEXIT_CRITICAL();
}

bool app_post_event(const app_msg_t *msg)
{
    if (xQueueSend(g_evq, msg, 0) != pdPASS) {
        g_uart_err++;                               /* dropped: counted, never blocks a sensor */
        return false;
    }
    return true;
}

bool app_post_tx(const app_msg_t *msg)
{
    if (xQueueSend(g_txq, msg, pdMS_TO_TICKS(20)) != pdPASS) {
        g_uart_err++;
        return false;
    }
    return true;
}

void app_start(void)
{
    /* DWT cycle counter for microsecond timestamps (beam direction). */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    g_evq = xQueueCreate(16, sizeof(app_msg_t));
    g_txq = xQueueCreate(12, sizeof(app_msg_t));
    g_actq = xQueueCreate(4, sizeof(act_req_t));
    g_edgeq = xQueueCreate(16, sizeof(edge_t));
    g_weight_mutex = xSemaphoreCreateMutex();
    i2c_bus_init();                             /* I2C1 mutex (MEMS + Environment tasks) */
    configASSERT(g_evq && g_txq && g_actq && g_edgeq && g_weight_mutex);

    for (unsigned s = 0; s < NODE_SLOTS; s++) {
        sm_weight_init(&g_weights[s], g_config.hx_offset[s], g_config.hx_num[s], g_config.hx_den[s]);
    }

    for (unsigned i = 0; i < TASK_COUNT; i++) {
        osThreadAttr_t attr;
        memset(&attr, 0, sizeof attr);
        attr.name = TASKS[i].name;
        attr.stack_size = TASKS[i].stack_bytes;
        attr.priority = TASKS[i].prio;
        g_tasks[i] = osThreadNew(TASKS[i].fn, NULL, &attr);
        configASSERT(g_tasks[i] != NULL);
    }
}

/* ------------------------------------------------------------------------- */
/* Interrupt glue                                                             */
/* ------------------------------------------------------------------------- */

void app_uart_rx_isr(uint8_t byte)
{
    uint16_t next = (uint16_t)((s_rx_head + 1u) % RX_RING);
    if (next == s_rx_tail) {
        g_rx_overflow++;
        return;
    }
    s_rx[s_rx_head] = byte;
    s_rx_head = next;
    BaseType_t woken = pdFALSE;
    if (g_tasks[TASK_UART] != NULL) {
        vTaskNotifyGiveFromISR((TaskHandle_t)g_tasks[TASK_UART], &woken);
    }
    portYIELD_FROM_ISR(woken);
}

bool app_rx_pop(uint8_t *byte)
{
    if (s_rx_tail == s_rx_head) {
        return false;
    }
    *byte = s_rx[s_rx_tail];
    s_rx_tail = (uint16_t)((s_rx_tail + 1u) % RX_RING);
    return true;
}

void app_edge_isr(uint16_t pin)
{
    BaseType_t woken = pdFALSE;
    edge_t e;
    e.t_us = app_us();
    switch (pin) {
    case BEAM_OUTER_PIN:
        e.src = EDGE_BEAM_OUTER;
        e.level = HAL_GPIO_ReadPin(BEAM_OUTER_PORT, BEAM_OUTER_PIN) == GPIO_PIN_SET;
        break;
    case BEAM_INNER_PIN:
        e.src = EDGE_BEAM_INNER;
        e.level = HAL_GPIO_ReadPin(BEAM_INNER_PORT, BEAM_INNER_PIN) == GPIO_PIN_SET;
        break;
    case PIR_PIN:
        e.src = EDGE_PIR;
        e.level = HAL_GPIO_ReadPin(PIR_PORT, PIR_PIN) == GPIO_PIN_SET;
        break;
    case RESTOCK_PIN:
        e.src = EDGE_RESTOCK;
        e.level = HAL_GPIO_ReadPin(RESTOCK_PORT, RESTOCK_PIN) == GPIO_PIN_SET;
        break;
    case HX711_1_DOUT_PIN:
    case HX711_2_DOUT_PIN:
        if (g_tasks[TASK_HX711] != NULL) {
            vTaskNotifyGiveFromISR((TaskHandle_t)g_tasks[TASK_HX711], &woken);
        }
        portYIELD_FROM_ISR(woken);
        return;
    case MEMS_INT_PIN:
        if (g_tasks[TASK_MEMS] != NULL) {
            vTaskNotifyGiveFromISR((TaskHandle_t)g_tasks[TASK_MEMS], &woken);
        }
        portYIELD_FROM_ISR(woken);
        return;
    default:
        return;
    }
    if (g_edgeq != NULL && xQueueSendFromISR(g_edgeq, &e, &woken) != pdPASS) {
        g_uart_err++;
    }
    portYIELD_FROM_ISR(woken);
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
    app_edge_isr(GPIO_Pin);
}

/* FreeRTOS hooks: a stack overflow or an empty heap is a bug; the watchdog
 * resets the node and the next $H says reset_cause=IWDG. */
void vApplicationStackOverflowHook(TaskHandle_t task, char *name)
{
    (void)task;
    (void)name;
    taskDISABLE_INTERRUPTS();
    for (;;) {
    }
}

void vApplicationMallocFailedHook(void)
{
    taskDISABLE_INTERRUPTS();
    for (;;) {
    }
}
