/*
 * tasks_core.c - Fusion/State, UART, Actuator and Health tasks.
 */
#include <string.h>

#include "app.h"
#include "board.h"
#include "drivers.h"
#include "node_config.h"

static const char *const SLOT_IDS[] = SM_SLOT_IDS;
static const char *const MEMS_IDS[] = SM_MEMS_IDS;
static const char MEMS_ROLES[] = SM_MEMS_ROLES;
static const char *const BEAM_IDS[] = SM_BEAM_IDS;

static int find_id(const char *const *ids, unsigned count, const char *id)
{
    for (unsigned i = 0; i < count; i++) {
        if (strcmp(ids[i], id) == 0) {
            return (int)i;
        }
    }
    return -1;
}

/* ------------------------------------------------------------------------- */
/* Fusion/State task: local patterns, then forward to the UART task.          */
/* ------------------------------------------------------------------------- */

void task_fusion(void *arg)
{
    (void)arg;
    for (;;) {
        app_msg_t m;
        app_checkin(TASK_FUSION);
        if (xQueueReceive(g_evq, &m, pdMS_TO_TICKS(500)) != pdPASS) {
            continue;
        }
        /* Weight gating at the source: while the shelf MEMS node says someone is
         * handling the shelf (TOUCH .. SETTLED), load-cell readings are flagged
         * unstable.  The Pi's fusion (Vinith, M6) applies the same rule again. */
        if (m.type == 'M' && m.u.m.node < NODE_MEMS_NODES && MEMS_ROLES[m.u.m.node] == 'S') {
            if (m.u.m.ev == SM_MEMS_TOUCH) {
                g_shelf_active = true;
            } else if (m.u.m.ev == SM_MEMS_SETTLED) {
                g_shelf_active = false;
            }
        }
        app_post_tx(&m);
    }
}

/* ------------------------------------------------------------------------- */
/* UART task: owns USART1.  TX: encode + send with the uplink seq.            */
/* RX: assemble command lines, apply them, answer every one with $K.          */
/* ------------------------------------------------------------------------- */

static uint8_t s_seq;
static sm_asm_t s_asm;

static size_t encode(const app_msg_t *m, char *line)
{
    switch (m->type) {
    case 'W':
        return sm_enc_weight(line, s_seq, m->ms, SLOT_IDS[m->u.w.slot % NODE_SLOTS], m->u.w.grams, m->u.w.stable);
    case 'M': {
        uint8_t n = (uint8_t)(m->u.m.node % NODE_MEMS_NODES);
        return sm_enc_mems(line, s_seq, m->ms, MEMS_IDS[n], MEMS_ROLES[n], (sm_mems_event_t)m->u.m.ev,
                           m->u.m.peak_mg, m->u.m.rms_mg, m->u.m.dur_ms, m->u.m.tilt_ddeg);
    }
    case 'B':
        return sm_enc_beam(line, s_seq, m->ms, BEAM_IDS[m->u.b.beam & 1u], m->u.b.clear);
    case 'D':
        return sm_enc_door(line, s_seq, m->ms, SM_DOOR_ID, m->u.d.in);
    case 'P':
        return sm_enc_presence(line, s_seq, m->ms, SM_PIR_ZONE, m->u.p.active);
    case 'E':
        return sm_enc_env(line, s_seq, m->ms, m->u.e.lux, m->u.e.temp_dc, m->u.e.rh_dpct, m->u.e.hpa_d);
    case 'R':
        return sm_enc_restock(line, s_seq, m->ms, SM_SHELF_ID);
    case 'H':
        return sm_enc_health(line, s_seq, m->ms, m->u.h.uptime_s, m->u.h.free_heap, m->u.h.min_stack_words,
                             m->u.h.i2c_err, m->u.h.uart_err, (sm_reset_t)m->u.h.cause);
    case 'K':
        return sm_enc_ack(line, s_seq, m->ms, m->u.k.cmd_seq, m->u.k.ok, m->u.k.code);
    default:
        return 0;
    }
}

static void send_msg(const app_msg_t *m)
{
    char line[SM_MAX_LINE + 1];
    size_t n = encode(m, line);
    if (n == 0u) {
        g_uart_err++;                       /* a message that cannot be encoded is a bug: count it */
        return;
    }
    s_seq++;
    if (HAL_UART_Transmit(&huart1, (uint8_t *)line, (uint16_t)n, 50) != HAL_OK) {
        g_uart_err++;
    }
}

static uint8_t act(uint8_t target, uint8_t pattern, uint8_t angle)
{
    act_req_t r = {target, pattern, angle};
    return xQueueSend(g_actq, &r, 0) == pdPASS ? SM_K_OK : SM_K_BUSY;
}

static uint8_t weight_cmd(const sm_cmd_t *c)
{
    int slot = find_id(SLOT_IDS, NODE_SLOTS, c->id);
    if (!node_enabled(SENS_HX711)) {
        return SM_K_SENSOR_DISABLED;
    }
    if (slot < 0 || (c->key == SM_CFG_CAL && c->value <= 0)) {
        return SM_K_BAD_ARG;
    }
    bool ok;
    xSemaphoreTake(g_weight_mutex, portMAX_DELAY);
    sm_weight_t *w = &g_weights[slot];
    ok = c->key == SM_CFG_TARE ? sm_weight_tare(w) : sm_weight_calibrate(w, c->value);
    if (ok) {
        g_config.hx_offset[slot] = w->offset;
        g_config.hx_num[slot] = w->scale_num;
        g_config.hx_den[slot] = w->scale_den;
    }
    xSemaphoreGive(g_weight_mutex);
    if (!ok) {
        return SM_K_BUSY;                   /* not enough samples yet, or no load for CAL */
    }
    node_config_save();
    return SM_K_OK;
}

static uint8_t apply(const sm_cmd_t *c)
{
    switch (c->type) {
    case 'S':
        g_epoch_ms_at_sync = c->epoch_ms;
        g_tick_at_sync = app_ms();
        return SM_K_OK;
    case 'L':
        return node_enabled(SENS_LED) ? act(ACT_LED, (uint8_t)c->pattern, 0) : SM_K_SENSOR_DISABLED;
    case 'Z':
        return node_enabled(SENS_BUZZER) ? act(ACT_BUZZER, (uint8_t)c->pattern, 0) : SM_K_SENSOR_DISABLED;
    case 'V':
        return node_enabled(SENS_SERVO) ? act(ACT_SERVO, 0, (uint8_t)c->angle) : SM_K_SENSOR_DISABLED;
    case 'C':
        break;
    default:
        return SM_K_UNKNOWN_CMD;
    }
    switch (c->key) {
    case SM_CFG_TARE:
    case SM_CFG_CAL:
        return weight_cmd(c);
    case SM_CFG_MEMS_THR: {
        int n = find_id(MEMS_IDS, NODE_MEMS_NODES, c->id);
        if (!node_enabled(SENS_MEMS)) {
            return SM_K_SENSOR_DISABLED;
        }
        if (n < 0 || c->value <= 0 || c->value > 16000) {
            return SM_K_BAD_ARG;
        }
        g_config.mems_thr_mg[n] = (uint16_t)c->value;
        node_config_save();
        return SM_K_OK;
    }
    case SM_CFG_MODE:
        /* Binary mode waits for the struct layout in PROTOCOL.md section 6. */
        return c->bin_mode ? SM_K_BAD_ARG : SM_K_OK;
    case SM_CFG_ENABLE: {
        int bit = node_sensor_bit(c->id);
        if (bit < 0) {
            return SM_K_BAD_ARG;
        }
        if (!node_sensor_compiled(bit)) {
            return SM_K_SENSOR_DISABLED;
        }
        node_set_enabled(bit, c->value != 0);
        node_config_save();
        return SM_K_OK;
    }
    default:
        return SM_K_BAD_ARG;
    }
}

static void handle_command(const char *line, size_t len)
{
    sm_cmd_t cmd;
    uint8_t code = SM_K_OK;
    sm_err_t err = sm_decode_cmd(line, len, &cmd, &code);
    if (!cmd.seq_valid) {
        g_uart_err++;                       /* unreadable: nothing to acknowledge */
        return;
    }
    if (err == SM_OK) {
        code = apply(&cmd);
    } else {
        g_uart_err++;
    }
    app_msg_t k;
    memset(&k, 0, sizeof k);
    k.type = 'K';
    k.ms = app_ms();
    k.u.k.cmd_seq = cmd.seq;
    k.u.k.ok = code == SM_K_OK;
    k.u.k.code = code;
    send_msg(&k);
}

void task_uart(void *arg)
{
    (void)arg;
    sm_asm_init(&s_asm);
    __HAL_UART_ENABLE_IT(&huart1, UART_IT_RXNE);
    for (;;) {
        app_msg_t m;
        uint8_t byte;
        app_checkin(TASK_UART);
        while (app_rx_pop(&byte)) {
            size_t n = sm_asm_push(&s_asm, byte);
            if (n != 0u) {
                handle_command(s_asm.buf, n);
            }
        }
        if (xQueueReceive(g_txq, &m, pdMS_TO_TICKS(5)) == pdPASS) {
            send_msg(&m);
        }
    }
}

uint32_t app_uart_errors(void)
{
    return g_uart_err + g_rx_overflow + s_asm.dropped;
}

/* ------------------------------------------------------------------------- */
/* Actuator task: non-blocking LED / buzzer patterns, servo                   */
/* ------------------------------------------------------------------------- */

void task_actuator(void *arg)
{
    (void)arg;
    sm_pat_t led;
    sm_pat_t buzzer;
    bool servo_started = false;
    sm_pat_set(&led, SM_PAT_OFF, 0, 0);
    sm_pat_set(&buzzer, SM_PAT_OFF, 0, 0);
    for (;;) {
        act_req_t r;
        app_checkin(TASK_ACTUATOR);
        while (xQueueReceive(g_actq, &r, 0) == pdPASS) {
            uint32_t now = app_ms();
            if (r.target == ACT_LED) {
                sm_pat_set(&led, (sm_pattern_t)r.pattern, now, 0);
            } else if (r.target == ACT_BUZZER) {
                sm_pat_set(&buzzer, (sm_pattern_t)r.pattern, now,
                           r.pattern == SM_PAT_OFF ? 0u : SM_BUZZER_AUTO_OFF_MS);
            } else if (r.target == ACT_SERVO) {
                if (!servo_started) {
                    HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_1);
                    servo_started = true;
                }
                /* 1 MHz timer, 20 ms period: 1000-2000 us pulse = 0-180 deg */
                __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_1, 1000u + (uint32_t)r.angle * 1000u / 180u);
            }
        }
        uint32_t now = app_ms();
        HAL_GPIO_WritePin(LED_ALERT_PORT, LED_ALERT_PIN,
                          sm_pat_output(&led, now) ? GPIO_PIN_SET : GPIO_PIN_RESET);
        HAL_GPIO_WritePin(BUZZER_PORT, BUZZER_PIN,
                          sm_pat_output(&buzzer, now) ? GPIO_PIN_SET : GPIO_PIN_RESET);
        osDelay(20);
    }
}

/* ------------------------------------------------------------------------- */
/* Health task: watchdog, heartbeat LED, $H every 10 s                        */
/* ------------------------------------------------------------------------- */

void task_health(void *arg)
{
    (void)arg;
    const uint32_t all = (1u << TASK_COUNT) - 1u;
    uint32_t next_h = app_ms() + 2000u;        /* first $H soon after boot */
    for (;;) {
        app_checkin(TASK_HEALTH);
        /* Kick the watchdog only when every task has checked in since the last
         * kick: a stuck task stops the kicks and the IWDG resets the node. */
        if ((g_alive & all) == all) {
            HAL_IWDG_Refresh(&hiwdg);
            taskENTER_CRITICAL();
            g_alive = 0;
            taskEXIT_CRITICAL();
        }
        HAL_GPIO_TogglePin(LED_STATUS_PORT, LED_STATUS_PIN);

        uint32_t now = app_ms();
        if ((int32_t)(now - next_h) >= 0) {
            next_h += SM_HEALTH_PERIOD_MS;
            uint32_t min_words = UINT32_MAX;
            for (unsigned i = 0; i < TASK_COUNT; i++) {
                if (g_tasks[i] != NULL) {
                    UBaseType_t w = uxTaskGetStackHighWaterMark((TaskHandle_t)g_tasks[i]);
                    min_words = w < min_words ? (uint32_t)w : min_words;
                }
            }
            app_msg_t h;
            memset(&h, 0, sizeof h);
            h.type = 'H';
            h.ms = now;
            h.u.h.uptime_s = now / 1000u;
            h.u.h.free_heap = (uint32_t)xPortGetFreeHeapSize();
            h.u.h.min_stack_words = min_words;
            h.u.h.i2c_err = g_i2c_err;
            h.u.h.uart_err = app_uart_errors();
            h.u.h.cause = (uint8_t)g_reset_cause;
            app_post_tx(&h);
        }
        osDelay(1000);
    }
}
