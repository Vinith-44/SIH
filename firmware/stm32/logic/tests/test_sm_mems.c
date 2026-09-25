/*
 * Host tests for the MEMS state machine, on synthetic 100 Hz accelerometer
 * streams (bucket C: our model of a shelf, not a measurement).
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "sm_mems.h"

static int g_checks;
static int g_failures;

#define CHECK(cond) do { \
    g_checks++; \
    if (!(cond)) { g_failures++; fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); } \
} while (0)

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* Deterministic noise, +-amp mg. */
static uint32_t g_rng = 12345u;
static int32_t noise(int32_t amp)
{
    g_rng = g_rng * 1103515245u + 12345u;
    return (int32_t)((g_rng >> 16) % (uint32_t)(2 * amp + 1)) - amp;
}

typedef struct {
    sm_mems_out_t ev[64];
    int n;
} log_t;

/* Run `ms` of samples at 100 Hz; gravity tilted by tilt_deg about x; plus a
 * vibration of `vib_mg` at 20 Hz during [vib_from, vib_to) and a single spike
 * of `spike_mg` lasting spike_ms at spike_at. */
typedef struct {
    double tilt_deg;
    int32_t vib_mg;
    uint32_t vib_from, vib_to;
    int32_t spike_mg;
    uint32_t spike_at, spike_ms;
    int32_t noise_mg;
} scene_t;

static uint32_t run(sm_mems_t *m, log_t *log, uint32_t t0, uint32_t ms, const scene_t *s)
{
    double rad = s->tilt_deg * M_PI / 180.0;
    for (uint32_t t = t0; t < t0 + ms; t += 10u) {
        double gx = 0.0;
        double gy = 1000.0 * sin(rad);
        double gz = 1000.0 * cos(rad);
        if (t >= s->vib_from && t < s->vib_to) {
            gx += s->vib_mg * sin(2.0 * M_PI * 20.0 * (double)t / 1000.0);
        }
        if (s->spike_mg && t >= s->spike_at && t < s->spike_at + s->spike_ms) {
            gx += s->spike_mg;
        }
        sm_mems_out_t out[2];
        int n = sm_mems_sample(m, (int32_t)gx + noise(s->noise_mg), (int32_t)gy + noise(s->noise_mg),
                               (int32_t)gz + noise(s->noise_mg), t, out);
        for (int i = 0; i < n && log->n < 64; i++) {
            log->ev[log->n++] = out[i];
        }
    }
    return t0 + ms;
}

static int count(const log_t *log, sm_mems_event_t ev)
{
    int c = 0;
    for (int i = 0; i < log->n; i++) {
        c += log->ev[i].ev == ev;
    }
    return c;
}

static void test_integer_maths(void)
{
    CHECK(sm_isqrt64(0) == 0 && sm_isqrt64(1) == 1 && sm_isqrt64(99) == 9 && sm_isqrt64(100) == 10);
    CHECK(sm_isqrt64(1000000000000ull) == 1000000u);
    int worst = 0;
    for (int deg10 = 0; deg10 <= 1800; deg10 += 5) {
        double r = deg10 / 10.0 * M_PI / 180.0;
        int32_t got = sm_atan2_ddeg((int64_t)(10000.0 * sin(r)), (int64_t)(10000.0 * cos(r)));
        int err = got - deg10;
        err = err < 0 ? -err : err;
        worst = err > worst ? err : worst;
    }
    CHECK(worst <= 3);                      /* within 0.3 deg over 0..180 deg */
    printf("atan2 worst error: %d (0.1 deg)\n", worst);
}

static void test_quiet_shelf_has_no_false_touch(void)
{
    sm_mems_t m;
    log_t log = {0};
    sm_mems_init(&m, false, 120);
    scene_t s = {0};
    s.noise_mg = 25;                        /* MPU6050 at +-2 g: a few mg of noise; 25 is generous */
    run(&m, &log, 0, 10u * 60u * 1000u, &s);
    CHECK(log.n == 0);                      /* acceptance: < 1 false TOUCH per 10 min idle */
    CHECK(m.have_ref);
}

static void test_touch_then_settled(void)
{
    sm_mems_t m;
    log_t log = {0};
    sm_mems_init(&m, false, 120);
    scene_t s = {0};
    s.noise_mg = 10;
    uint32_t t = run(&m, &log, 0, 3000, &s);            /* reference */
    s.vib_mg = 350;
    s.vib_from = t + 100;
    s.vib_to = t + 900;                                  /* a hand for 0.8 s */
    run(&m, &log, t, 3000, &s);
    CHECK(log.n == 2);
    CHECK(log.ev[0].ev == SM_MEMS_TOUCH && log.ev[0].peak_mg > 200);
    CHECK(log.ev[1].ev == SM_MEMS_SETTLED);
    CHECK(log.ev[1].dur_ms >= 1100 && log.ev[1].dur_ms <= 1600);  /* 0.8 s + 0.5 s settle */
    CHECK(log.ev[1].peak_mg < 120);                     /* SETTLED reports the calm, not the touch */
}

static void test_knock_is_not_a_touch(void)
{
    sm_mems_t m;
    log_t log = {0};
    sm_mems_init(&m, false, 120);
    scene_t s = {0};
    s.noise_mg = 10;
    uint32_t t = run(&m, &log, 0, 3000, &s);
    s.spike_mg = 1500;
    s.spike_at = t + 200;
    s.spike_ms = 40;                                     /* a trolley bump */
    run(&m, &log, t, 2000, &s);
    CHECK(count(&log, SM_MEMS_KNOCK) == 1);
    CHECK(count(&log, SM_MEMS_TOUCH) == 0);
    CHECK(log.n == 1 && log.ev[0].peak_mg >= 800);
}

static void test_camera_mount(void)
{
    sm_mems_t m;
    log_t log = {0};
    sm_mems_init(&m, true, 600);
    scene_t s = {0};
    s.noise_mg = 10;
    uint32_t t = run(&m, &log, 0, 3000, &s);
    s.vib_mg = 300;                                      /* someone brushing the pole: ignore */
    s.vib_from = t;
    s.vib_to = t + 1000;
    t = run(&m, &log, t, 3000, &s);
    CHECK(log.n == 0);
    s.vib_mg = 0;
    s.spike_mg = 1850;
    s.spike_at = t + 100;
    s.spike_ms = 40;                                     /* a knock on the bracket */
    run(&m, &log, t, 2000, &s);
    CHECK(log.n == 1 && log.ev[0].ev == SM_MEMS_KNOCK);
}

static void test_tilt(void)
{
    sm_mems_t m;
    log_t log = {0};
    sm_mems_init(&m, false, 120);
    scene_t s = {0};
    s.noise_mg = 10;
    uint32_t t = run(&m, &log, 0, 3000, &s);
    s.tilt_deg = 6.2;                                    /* the PROTOCOL.md example: 62 */
    t = run(&m, &log, t, 6000, &s);
    CHECK(count(&log, SM_MEMS_TILT) == 1);
    int tilt_at = -1;
    for (int i = 0; i < log.n; i++) {
        if (log.ev[i].ev == SM_MEMS_TILT) {
            tilt_at = i;
        }
    }
    CHECK(tilt_at >= 0 && log.ev[tilt_at].tilt_ddeg >= 55 && log.ev[tilt_at].tilt_ddeg <= 69);
    /* Stays tilted: no repeat.  Back level: re-armed, and tilting again fires again. */
    t = run(&m, &log, t, 5000, &s);
    CHECK(count(&log, SM_MEMS_TILT) == 1);
    s.tilt_deg = 0.0;
    t = run(&m, &log, t, 5000, &s);
    s.tilt_deg = 8.0;
    run(&m, &log, t, 6000, &s);
    CHECK(count(&log, SM_MEMS_TILT) == 2);

    /* A 1 deg lean (within threshold) never fires. */
    sm_mems_t c;
    log_t quiet = {0};
    sm_mems_init(&c, false, 120);
    scene_t level = {0};
    t = run(&c, &quiet, 0, 3000, &level);
    level.tilt_deg = 1.0;
    run(&c, &quiet, t, 10000, &level);
    CHECK(quiet.n == 0);
}

static void test_threshold_command(void)
{
    sm_mems_t m;
    log_t log = {0};
    sm_mems_init(&m, false, 120);
    sm_mems_set_threshold(&m, 500);                      /* $C,MEMS_THR,m1,500 */
    scene_t s = {0};
    s.noise_mg = 10;
    uint32_t t = run(&m, &log, 0, 3000, &s);
    s.vib_mg = 350;                                      /* was a touch at 120 mg, not at 500 */
    s.vib_from = t;
    s.vib_to = t + 800;
    run(&m, &log, t, 3000, &s);
    CHECK(log.n == 0);
    CHECK(m.knock_mg == 2000);
}

int main(void)
{
    test_integer_maths();
    test_quiet_shelf_has_no_false_touch();
    test_touch_then_settled();
    test_knock_is_not_a_touch();
    test_camera_mount();
    test_tilt();
    test_threshold_command();
    printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? 0 : 1;
}
