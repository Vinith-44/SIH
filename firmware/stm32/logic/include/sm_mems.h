/*
 * sm_mems.h - MEMS accelerometer state machine (M6), portable and integer-only.
 *
 * Analogy: the camera sees, the load cell weighs, the MEMS chip under the shelf
 * *feels* when someone touches or bumps it.  This file turns raw accelerations
 * (milli-g, ~100 Hz) into the four $M events of docs/PROTOCOL.md:
 *
 *   IDLE --burst > thr--> CANDIDATE --lasts > knock_ms--> ACTIVE --quiet--> SETTLING --quiet settle_ms--> IDLE
 *                             |  emits TOUCH at the knock_ms mark        (activity -> back to ACTIVE)  emits SETTLED
 *                             '--ends within knock_ms, peak >= knock_mg--> KNOCK
 *   Any state: the gravity direction moved > tilt_thr for tilt_hold_ms --> TILT (once, re-armed on return)
 *
 * Why wait knock_ms (150 ms) before TOUCH: a trolley bump is a single short spike,
 * a hand on a shelf lasts longer.  Waiting 150 ms tells them apart, and is still far
 * shorter than a pick (the load cell needs ~1 s to settle anyway).
 *
 * Camera-mount nodes (role C) only report KNOCK and TILT: a touch on a camera
 * bracket is not a shopper event (PROTOCOL.md drops it anyway, so it is not sent).
 */
#ifndef SM_MEMS_H
#define SM_MEMS_H

#include <stdbool.h>
#include <stdint.h>

#include "sm_proto.h"   /* sm_mems_event_t, SM_NONE */

#ifdef __cplusplus
extern "C" {
#endif

#define SM_MEMS_WINDOW 5u          /* samples per decision window (50 ms at 100 Hz) */

typedef enum { SM_MS_IDLE = 0, SM_MS_CANDIDATE, SM_MS_ACTIVE, SM_MS_SETTLING } sm_mems_state_t;

typedef struct {
    /* settings (sm_mems_init fills defaults; $C,MEMS_THR changes thr_mg) */
    bool camera;                   /* role C: KNOCK / TILT only */
    int32_t thr_mg;                /* activity threshold (shelf 120, camera 600) */
    int32_t knock_mg;              /* a short burst at least this strong is a KNOCK */
    uint32_t knock_ms;             /* bursts shorter than this can be knocks (150) */
    uint32_t settle_ms;            /* quiet this long after activity = SETTLED (500) */
    int32_t tilt_thr_ddeg;         /* 0.1 deg; shelf 50 (5 deg), camera 20 (2 deg) */
    uint32_t tilt_hold_ms;         /* the tilt must last this long (2000) */
    uint32_t ref_quiet_ms;         /* quiet time at boot before the reference is taken (2000) */

    /* gravity estimate (EMA, mg x 256) and the boot reference orientation */
    int32_t g_q8[3];
    bool have_g;
    int32_t ref[3];
    bool have_ref;
    uint32_t idle_since;

    /* current window */
    uint8_t win_n;
    int32_t win_peak;
    uint64_t win_sumsq;

    /* episode */
    sm_mems_state_t state;
    uint32_t start_ms;
    uint32_t quiet_since;
    int32_t ep_peak;
    uint64_t ep_sumsq;
    uint32_t ep_n;
    int32_t settle_peak;
    uint64_t settle_sumsq;
    uint32_t settle_n;

    /* tilt */
    bool tilted;
    uint32_t tilt_since;
    bool tilt_pending;
    int32_t last_tilt_ddeg;
} sm_mems_t;

typedef struct {
    sm_mems_event_t ev;
    int32_t peak_mg;
    int32_t rms_mg;
    int32_t dur_ms;
    int32_t tilt_ddeg;             /* SM_NONE unless ev == TILT */
} sm_mems_out_t;

void sm_mems_init(sm_mems_t *m, bool camera, int32_t thr_mg);
void sm_mems_set_threshold(sm_mems_t *m, int32_t thr_mg);
/* Feed one sample (mg).  Writes up to 2 events into out[]; returns how many. */
int sm_mems_sample(sm_mems_t *m, int32_t ax, int32_t ay, int32_t az, uint32_t now_ms, sm_mems_out_t out[2]);
/* Angle between the current gravity estimate and the boot reference, 0.1 deg
 * (SM_NONE before the reference exists). */
int32_t sm_mems_tilt_ddeg(const sm_mems_t *m);

/* Integer helpers, exposed for the host tests. */
uint32_t sm_isqrt64(uint64_t v);
int32_t sm_atan2_ddeg(int64_t y, int64_t x);     /* 0.1 deg, y >= 0; result 0..1800 */

#ifdef __cplusplus
}
#endif

#endif /* SM_MEMS_H */
