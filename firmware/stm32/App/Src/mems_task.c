/*
 * mems_task.c - MEMS task (M6): two MPU6050 accelerometers on I2C1, 100 Hz.
 *
 *   m1 at 0x68 (AD0 low)  -> under shelf-a        (role S: TOUCH / SETTLED / KNOCK / TILT)
 *   m2 at 0x69 (AD0 high) -> on the entrance camera bracket (role C: KNOCK / TILT)
 *
 * Every 10 ms both chips are read (6 bytes each, ~0.8 ms per read at 100 kHz)
 * and each sample goes through the portable state machine (logic/sm_mems.c).
 * Only *events* go to the Pi, never raw samples: 100 Hz x 2 nodes of raw data
 * would not fit the UART budget or the 20 KB of RAM.
 *
 * MPU6050 register map: InvenSense RM-MPU-6000A-00 rev 4.2.  Settings: +-2 g
 * (16384 LSB/g), DLPF 44 Hz, 100 Hz sample rate.  The INT pin (PB5) is wired but
 * the task polls on a fixed 10 ms period, which keeps both nodes in step.
 */
#include <string.h>

#include "app.h"
#include "drivers.h"
#include "node_config.h"
#include "sm_mems.h"

#define MPU_REG_SMPLRT_DIV    0x19u
#define MPU_REG_CONFIG        0x1Au
#define MPU_REG_ACCEL_CONFIG  0x1Cu
#define MPU_REG_ACCEL_XOUT_H  0x3Bu
#define MPU_REG_PWR_MGMT_1    0x6Bu
#define MPU_REG_WHO_AM_I      0x75u
#define MPU_WHO_AM_I          0x68u

static const uint8_t ADDR[NODE_MEMS_NODES] = {0x68u, 0x69u};
static const char ROLE[NODE_MEMS_NODES] = SM_MEMS_ROLES;

bool mpu6050_init(uint8_t addr)
{
    uint8_t who = 0;
    if (!i2c_bus_read(addr, MPU_REG_WHO_AM_I, &who, 1) || (who & 0x7Eu) != MPU_WHO_AM_I) {
        return false;                                /* absent, or not an MPU6050 */
    }
    return i2c_bus_write(addr, MPU_REG_PWR_MGMT_1, 0x01u) &&   /* wake, PLL on gyro X */
           i2c_bus_write(addr, MPU_REG_CONFIG, 0x03u) &&       /* DLPF 44 Hz -> 1 kHz internal */
           i2c_bus_write(addr, MPU_REG_SMPLRT_DIV, 9u) &&      /* 1 kHz / (1 + 9) = 100 Hz */
           i2c_bus_write(addr, MPU_REG_ACCEL_CONFIG, 0x00u);   /* +-2 g */
}

bool mpu6050_read_mg(uint8_t addr, int32_t mg[3])
{
    uint8_t b[6];
    if (!i2c_bus_read(addr, MPU_REG_ACCEL_XOUT_H, b, sizeof b)) {
        return false;
    }
    for (int i = 0; i < 3; i++) {
        int16_t raw = (int16_t)((b[2 * i] << 8) | b[2 * i + 1]);
        mg[i] = ((int32_t)raw * 1000) / 16384;
    }
    return true;
}

void task_mems(void *arg)
{
    (void)arg;
    static sm_mems_t sm[NODE_MEMS_NODES];      /* static: ~300 B would not fit the 512 B task stack */
    bool present[NODE_MEMS_NODES] = {false, false};
    uint16_t thr_in_use[NODE_MEMS_NODES] = {0, 0};
    uint32_t next_probe = 0;
    TickType_t wake = xTaskGetTickCount();

    for (unsigned n = 0; n < NODE_MEMS_NODES; n++) {
        sm_mems_init(&sm[n], ROLE[n] == 'C', g_config.mems_thr_mg[n]);
        thr_in_use[n] = g_config.mems_thr_mg[n];
    }

    for (;;) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(10));
        app_checkin(TASK_MEMS);
        if (!node_enabled(SENS_MEMS)) {
            continue;
        }
        uint32_t now = app_ms();
        /* A missing chip is re-probed every 5 s, so plugging it in later works. */
        if ((int32_t)(now - next_probe) >= 0) {
            next_probe = now + 5000u;
            for (unsigned n = 0; n < NODE_MEMS_NODES; n++) {
                if (!present[n]) {
                    present[n] = mpu6050_init(ADDR[n]);
                }
            }
        }
        for (unsigned n = 0; n < NODE_MEMS_NODES; n++) {
            if (!present[n]) {
                continue;
            }
            if (thr_in_use[n] != g_config.mems_thr_mg[n]) {        /* $C,MEMS_THR arrived */
                thr_in_use[n] = g_config.mems_thr_mg[n];
                sm_mems_set_threshold(&sm[n], thr_in_use[n]);
            }
            int32_t a[3];
            if (!mpu6050_read_mg(ADDR[n], a)) {
                present[n] = false;                               /* re-probe later */
                continue;
            }
            sm_mems_out_t out[2];
            int count = sm_mems_sample(&sm[n], a[0], a[1], a[2], now, out);
            for (int i = 0; i < count; i++) {
                app_msg_t m;
                memset(&m, 0, sizeof m);
                m.type = 'M';
                m.ms = now;
                m.u.m.node = (uint8_t)n;
                m.u.m.ev = (uint8_t)out[i].ev;
                m.u.m.peak_mg = out[i].peak_mg;
                m.u.m.rms_mg = out[i].rms_mg;
                m.u.m.dur_ms = out[i].dur_ms;
                m.u.m.tilt_ddeg = out[i].tilt_ddeg;
                app_post_event(&m);
            }
        }
    }
}
