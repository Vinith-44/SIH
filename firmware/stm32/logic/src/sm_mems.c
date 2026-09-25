/*
 * sm_mems.c - MEMS state machine (see sm_mems.h).  Portable C99, integer-only.
 */
#include "sm_mems.h"

#include <string.h>

/* ------------------------------------------------------------------------- */
/* Integer maths                                                              */
/* ------------------------------------------------------------------------- */

uint32_t sm_isqrt64(uint64_t v)
{
    uint64_t res = 0;
    uint64_t bit = (uint64_t)1 << 62;
    while (bit > v) {
        bit >>= 2;
    }
    while (bit != 0u) {
        if (v >= res + bit) {
            v -= res + bit;
            res = (res >> 1) + bit;
        } else {
            res >>= 1;
        }
        bit >>= 2;
    }
    return (uint32_t)res;
}

/* atan(z) for z = n/d in [0, 1], in 0.1 deg.  atan(z) ~ 45 z + 15.64 z (1 - z)
 * degrees (max error ~0.2 deg), evaluated in Q15. */
static int32_t atan_unit_ddeg(uint64_t n, uint64_t d)
{
    if (d == 0u) {
        return 450;
    }
    int64_t z = (int64_t)((n << 15) / d);                 /* 0..32768 */
    int64_t deg10 = (450 * z + (1564 * z * (32768 - z) / 10 / 32768)) / 32768;
    return (int32_t)deg10;
}

int32_t sm_atan2_ddeg(int64_t y, int64_t x)
{
    uint64_t ay = (uint64_t)(y < 0 ? -y : y);
    uint64_t ax = (uint64_t)(x < 0 ? -x : x);
    int32_t a = ay <= ax ? atan_unit_ddeg(ay, ax) : 900 - atan_unit_ddeg(ax, ay);
    return x < 0 ? 1800 - a : a;
}

/* ------------------------------------------------------------------------- */
/* State machine                                                              */
/* ------------------------------------------------------------------------- */

void sm_mems_set_threshold(sm_mems_t *m, int32_t thr_mg)
{
    m->thr_mg = thr_mg > 0 ? thr_mg : 1;
    /* On a camera bracket the threshold already is a knock level (600 mg);
     * under a shelf a knock must be well above a hand (4x, at least 800 mg). */
    m->knock_mg = m->camera ? m->thr_mg : (m->thr_mg * 4 > 800 ? m->thr_mg * 4 : 800);
}

void sm_mems_init(sm_mems_t *m, bool camera, int32_t thr_mg)
{
    memset(m, 0, sizeof *m);
    m->camera = camera;
    sm_mems_set_threshold(m, thr_mg);   /* needs m->camera set first */
    m->knock_ms = 150u;
    m->settle_ms = 500u;
    m->tilt_thr_ddeg = camera ? 20 : 50;
    m->tilt_hold_ms = 2000u;
    m->ref_quiet_ms = 2000u;
    m->state = SM_MS_IDLE;
}

static int32_t rms_of(uint64_t sumsq, uint32_t n)
{
    return n == 0u ? 0 : (int32_t)sm_isqrt64(sumsq / n);
}

int32_t sm_mems_tilt_ddeg(const sm_mems_t *m)
{
    if (!m->have_ref || !m->have_g) {
        return SM_NONE;
    }
    int64_t g[3];
    for (int i = 0; i < 3; i++) {
        g[i] = m->g_q8[i] / 256;
    }
    int64_t r0 = m->ref[0], r1 = m->ref[1], r2 = m->ref[2];
    int64_t dot = g[0] * r0 + g[1] * r1 + g[2] * r2;
    int64_t c0 = g[1] * r2 - g[2] * r1;
    int64_t c1 = g[2] * r0 - g[0] * r2;
    int64_t c2 = g[0] * r1 - g[1] * r0;
    uint64_t cross = sm_isqrt64((uint64_t)(c0 * c0) + (uint64_t)(c1 * c1) + (uint64_t)(c2 * c2));
    return sm_atan2_ddeg((int64_t)cross, dot);
}

static void emit(sm_mems_out_t *o, sm_mems_event_t ev, int32_t peak, int32_t rms, uint32_t dur, int32_t tilt)
{
    o->ev = ev;
    o->peak_mg = peak;
    o->rms_mg = rms;
    o->dur_ms = (int32_t)dur;
    o->tilt_ddeg = tilt;
}

/* One 50 ms window has been completed: run the state machine on it. */
static int on_window(sm_mems_t *m, uint32_t now, sm_mems_out_t out[2])
{
    int n = 0;
    int32_t peak = m->win_peak;
    uint64_t sumsq = m->win_sumsq;
    bool active = peak > m->thr_mg;
    bool quiet = !active;

    switch (m->state) {
    case SM_MS_IDLE:
        if (active) {
            m->state = SM_MS_CANDIDATE;
            m->start_ms = now;
            m->ep_peak = peak;
            m->ep_sumsq = sumsq;
            m->ep_n = SM_MEMS_WINDOW;
        }
        break;
    case SM_MS_CANDIDATE:
        if (quiet) {
            /* The burst ended within knock_ms. */
            uint32_t dur = now - m->start_ms;
            if (m->ep_peak >= m->knock_mg) {
                emit(&out[n++], SM_MEMS_KNOCK, m->ep_peak, rms_of(m->ep_sumsq, m->ep_n), dur, SM_NONE);
                m->state = SM_MS_IDLE;
            } else if (!m->camera) {
                /* A light, short tap: a touch that settles right away. */
                emit(&out[n++], SM_MEMS_TOUCH, m->ep_peak, rms_of(m->ep_sumsq, m->ep_n), dur, SM_NONE);
                m->state = SM_MS_SETTLING;
                m->quiet_since = now;
                m->settle_peak = peak;
                m->settle_sumsq = sumsq;
                m->settle_n = SM_MEMS_WINDOW;
            } else {
                m->state = SM_MS_IDLE;
            }
            break;
        }
        m->ep_peak = peak > m->ep_peak ? peak : m->ep_peak;
        m->ep_sumsq += sumsq;
        m->ep_n += SM_MEMS_WINDOW;
        if (now - m->start_ms >= m->knock_ms) {
            if (!m->camera) {
                emit(&out[n++], SM_MEMS_TOUCH, m->ep_peak, rms_of(m->ep_sumsq, m->ep_n),
                     now - m->start_ms, SM_NONE);
            }
            m->state = SM_MS_ACTIVE;
        }
        break;
    case SM_MS_ACTIVE:
        m->ep_peak = peak > m->ep_peak ? peak : m->ep_peak;
        m->ep_sumsq += sumsq;
        m->ep_n += SM_MEMS_WINDOW;
        if (quiet) {
            m->state = SM_MS_SETTLING;
            m->quiet_since = now;
            m->settle_peak = peak;
            m->settle_sumsq = sumsq;
            m->settle_n = SM_MEMS_WINDOW;
        }
        break;
    case SM_MS_SETTLING:
        if (active) {
            m->state = SM_MS_ACTIVE;
            m->ep_peak = peak > m->ep_peak ? peak : m->ep_peak;
            break;
        }
        m->settle_peak = peak > m->settle_peak ? peak : m->settle_peak;
        m->settle_sumsq += sumsq;
        m->settle_n += SM_MEMS_WINDOW;
        if (now - m->quiet_since >= m->settle_ms) {
            if (m->camera) {
                /* A long vibration on a camera bracket: a knock if it was strong. */
                if (m->ep_peak >= m->knock_mg) {
                    emit(&out[n++], SM_MEMS_KNOCK, m->ep_peak, rms_of(m->ep_sumsq, m->ep_n),
                         m->quiet_since - m->start_ms, SM_NONE);
                }
            } else {
                emit(&out[n++], SM_MEMS_SETTLED, m->settle_peak, rms_of(m->settle_sumsq, m->settle_n),
                     now - m->start_ms, SM_NONE);
            }
            m->state = SM_MS_IDLE;
        }
        break;
    default:
        m->state = SM_MS_IDLE;
        break;
    }

    /* Reference orientation: the gravity direction after ref_quiet_ms of calm. */
    if (m->state == SM_MS_IDLE && quiet) {
        if (m->idle_since == 0u) {
            m->idle_since = now ? now : 1u;
        }
        if (!m->have_ref && now - m->idle_since >= m->ref_quiet_ms) {
            for (int i = 0; i < 3; i++) {
                m->ref[i] = m->g_q8[i] / 256;
            }
            m->have_ref = true;
        }
    } else {
        m->idle_since = 0;
    }

    /* Tilt: sustained change of the gravity direction. */
    int32_t tilt = sm_mems_tilt_ddeg(m);
    if (tilt != SM_NONE && n < 2) {
        m->last_tilt_ddeg = tilt;
        if (!m->tilted && tilt >= m->tilt_thr_ddeg) {
            if (!m->tilt_pending) {
                m->tilt_pending = true;
                m->tilt_since = now;
            } else if (now - m->tilt_since >= m->tilt_hold_ms) {
                emit(&out[n++], SM_MEMS_TILT, peak, rms_of(sumsq, SM_MEMS_WINDOW), now - m->tilt_since, tilt);
                m->tilted = true;
                m->tilt_pending = false;
            }
        } else if (tilt < m->tilt_thr_ddeg) {
            m->tilt_pending = false;
            if (m->tilted && tilt < m->tilt_thr_ddeg / 2) {
                m->tilted = false;             /* back in place: re-arm */
            }
        }
    }
    return n;
}

int sm_mems_sample(sm_mems_t *m, int32_t ax, int32_t ay, int32_t az, uint32_t now_ms, sm_mems_out_t out[2])
{
    const int32_t a[3] = {ax, ay, az};
    if (!m->have_g) {
        for (int i = 0; i < 3; i++) {
            m->g_q8[i] = a[i] * 256;
        }
        m->have_g = true;
    }
    /* Dynamic part = sample - slow gravity estimate (EMA, ~0.64 s at 100 Hz). */
    int64_t sq = 0;
    for (int i = 0; i < 3; i++) {
        int32_t d = a[i] - m->g_q8[i] / 256;
        sq += (int64_t)d * d;
        m->g_q8[i] += (a[i] * 256 - m->g_q8[i]) / 64;
    }
    int32_t mag = (int32_t)sm_isqrt64((uint64_t)sq);
    m->win_peak = mag > m->win_peak ? mag : m->win_peak;
    m->win_sumsq += (uint64_t)sq;
    if (++m->win_n < SM_MEMS_WINDOW) {
        return 0;
    }
    int n = on_window(m, now_ms, out);
    m->win_n = 0;
    m->win_peak = 0;
    m->win_sumsq = 0;
    return n;
}
