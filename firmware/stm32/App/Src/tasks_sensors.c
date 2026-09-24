/*
 * tasks_sensors.c - HX711, MEMS, Presence and Environment tasks.
 * Each task turns hardware into app_msg_t events for the Fusion/State task and
 * checks in with the Health task at least once a second.
 */
#include <string.h>

#include "app.h"
#include "board.h"
#include "drivers.h"
#include "node_config.h"

/* ------------------------------------------------------------------------- */
/* HX711 task: load cells -> $W                                               */
/* ------------------------------------------------------------------------- */

static const hx711_t HX[NODE_SLOTS] = {
    {HX711_1_DOUT_PORT, HX711_1_DOUT_PIN, HX711_1_SCK_PORT, HX711_1_SCK_PIN},
    {HX711_2_DOUT_PORT, HX711_2_DOUT_PIN, HX711_2_SCK_PORT, HX711_2_SCK_PIN},
};

void task_hx711(void *arg)
{
    (void)arg;
    for (;;) {
        /* DOUT falling edge (EXTI) wakes us; the timeout keeps the check-in going
         * even when no load cell is connected. */
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(200));
        app_checkin(TASK_HX711);
        if (!node_enabled(SENS_HX711)) {
            continue;
        }
        for (uint8_t s = 0; s < NODE_SLOTS; s++) {
            if (!hx711_ready(&HX[s])) {
                continue;
            }
            int32_t raw = hx711_read(&HX[s]);
            int32_t grams = 0;
            bool stable = false;
            bool due;
            xSemaphoreTake(g_weight_mutex, portMAX_DELAY);
            due = sm_weight_push(&g_weights[s], raw, app_ms(), g_shelf_active, &grams, &stable);
            xSemaphoreGive(g_weight_mutex);
            if (due) {
                app_msg_t m;
                memset(&m, 0, sizeof m);
                m.type = 'W';
                m.ms = app_ms();
                m.u.w.slot = s;
                m.u.w.grams = grams;
                m.u.w.stable = stable;
                app_post_event(&m);
            }
        }
    }
}

/* ------------------------------------------------------------------------- */
/* MEMS task: filled in by M6 (task_mems in mems_task.c when SM_HAVE_MEMS).   */
/* ------------------------------------------------------------------------- */

__attribute__((weak)) void task_mems(void *arg)
{
    (void)arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500));
        app_checkin(TASK_MEMS);
    }
}

/* ------------------------------------------------------------------------- */
/* Presence task: IR beams -> $B / $D, PIR -> $P, restock button -> $R         */
/* ------------------------------------------------------------------------- */

void task_presence(void *arg)
{
    (void)arg;
    sm_beam_t beam;
    sm_deb_t pir;
    sm_deb_t button;
    bool pir_level = false;
    bool button_level = true;               /* pull-up: released = high */
    uint32_t last_restock = 0;
    sm_beam_init(&beam, 3000000u, 2000u);
    sm_deb_init(&pir, false, 200u);
    sm_deb_init(&button, true, 50u);

    for (;;) {
        edge_t e;
        app_checkin(TASK_PRESENCE);
        if (xQueueReceive(g_edgeq, &e, pdMS_TO_TICKS(50)) == pdPASS) {
            app_msg_t m;
            memset(&m, 0, sizeof m);
            m.ms = app_ms();
            switch (e.src) {
            case EDGE_BEAM_OUTER:
            case EDGE_BEAM_INNER: {
                if (!node_enabled(SENS_IR_BEAM)) {
                    break;
                }
                bool blocked = !e.level;    /* receiver output low = beam broken */
                uint8_t which = e.src == EDGE_BEAM_OUTER ? 0u : 1u;
#if SM_SEND_BEAM_EDGES
                m.type = 'B';
                m.u.b.beam = which;
                m.u.b.clear = !blocked;
                app_post_event(&m);
#endif
                sm_dir_t dir = sm_beam_edge(&beam, which, blocked, e.t_us);
                if (dir != SM_DIR_NONE) {
                    m.type = 'D';
                    m.u.d.in = dir == SM_DIR_IN;
                    app_post_event(&m);
                }
                break;
            }
            case EDGE_PIR:
                pir_level = e.level;
                break;
            case EDGE_RESTOCK:
                button_level = e.level;
                break;
            default:
                break;
            }
        }
        uint32_t now = app_ms();
        if (sm_deb_update(&pir, pir_level, now) && node_enabled(SENS_PIR)) {
            app_msg_t m;
            memset(&m, 0, sizeof m);
            m.type = 'P';
            m.ms = now;
            m.u.p.active = pir.state;
            app_post_event(&m);
        }
        if (sm_deb_update(&button, button_level, now) && !button.state && node_enabled(SENS_RESTOCK) &&
            now - last_restock > 1000u) {
            last_restock = now;
            app_msg_t m;
            memset(&m, 0, sizeof m);
            m.type = 'R';
            m.ms = now;
            app_post_event(&m);
        }
    }
}

/* ------------------------------------------------------------------------- */
/* Environment task: BH1750 every 1 s, BME280 + $E every 5 s                  */
/* ------------------------------------------------------------------------- */

void task_env(void *arg)
{
    (void)arg;
    int32_t lux = SM_NONE;
    bool bh_ok = false;
    bool bme_ok = false;
    uint32_t next_env = app_ms() + 1000u;
    for (;;) {
        app_checkin(TASK_ENV);
        if (node_enabled(SENS_BH1750)) {
            if (!bh_ok) {
                bh_ok = bh1750_init();
            }
            if (bh_ok && !bh1750_read_lux(&lux)) {
                bh_ok = false;
                lux = SM_NONE;
            }
        } else {
            lux = SM_NONE;
        }
        uint32_t now = app_ms();
        if ((int32_t)(now - next_env) >= 0) {
            next_env += SM_ENV_PERIOD_MS;
            int32_t t = SM_NONE;
            int32_t rh = SM_NONE;
            int32_t p = SM_NONE;
            if (node_enabled(SENS_BME280)) {
                if (!bme_ok) {
                    bme_ok = bme280_init();
                }
                if (bme_ok && !bme280_read(&t, &rh, &p)) {
                    bme_ok = false;
                    t = rh = p = SM_NONE;
                }
            }
            if (node_enabled(SENS_BH1750) || node_enabled(SENS_BME280)) {
                app_msg_t m;
                memset(&m, 0, sizeof m);
                m.type = 'E';
                m.ms = now;
                m.u.e.lux = lux;
                m.u.e.temp_dc = t;
                m.u.e.rh_dpct = rh;
                m.u.e.hpa_d = p;
                app_post_event(&m);
            }
        }
        osDelay(SM_LUX_PERIOD_MS);
    }
}
