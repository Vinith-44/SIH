/* Host unit tests for sm_logic (patterns, beams, load cell, debounce). */
#include <stdio.h>
#include <string.h>

#include "sm_logic.h"

static int g_checks;
static int g_failures;

#define CHECK(cond) do { \
    g_checks++; \
    if (!(cond)) { g_failures++; fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); } \
} while (0)

static unsigned on_ms(sm_pat_t *p, uint32_t from, uint32_t to)
{
    unsigned on = 0;
    for (uint32_t t = from; t < to; t++) {
        on += sm_pat_output(p, t) ? 1u : 0u;
    }
    return on;
}

static void test_patterns(void)
{
    sm_pat_t p;
    sm_pat_set(&p, SM_PAT_OFF, 0, 0);
    CHECK(on_ms(&p, 0, 1000) == 0);
    sm_pat_set(&p, SM_PAT_ON, 0, 0);
    CHECK(on_ms(&p, 0, 1000) == 1000);
    sm_pat_set(&p, SM_PAT_SLOW, 0, 0);
    CHECK(on_ms(&p, 0, 2000) == 1000);          /* 50% duty */
    CHECK(sm_pat_output(&p, 100) && !sm_pat_output(&p, 700));
    sm_pat_set(&p, SM_PAT_FAST, 0, 0);
    CHECK(on_ms(&p, 0, 1000) == 500);            /* 4 Hz, 50% */
    sm_pat_set(&p, SM_PAT_ALERT, 0, 0);
    CHECK(on_ms(&p, 0, 1000) == 300);            /* 3 x 100 ms */
    CHECK(!sm_pat_output(&p, 700));              /* the quiet gap */

    /* Buzzer auto-off: ALERT for 3 s, then silent until told otherwise. */
    sm_pat_set(&p, SM_PAT_ALERT, 5000, 3000);
    CHECK(on_ms(&p, 5000, 8000) == 900);
    CHECK(on_ms(&p, 8000, 12000) == 0);
    CHECK(p.pattern == SM_PAT_OFF);

    /* Survives the 49-day uptime wrap. */
    sm_pat_set(&p, SM_PAT_SLOW, UINT32_MAX - 100u, 0);
    CHECK(sm_pat_output(&p, UINT32_MAX - 50u));
    CHECK(sm_pat_output(&p, 200u));               /* 301 ms after start */
}

static sm_dir_t walk(sm_beam_t *b, uint8_t first, uint32_t t0, uint32_t gap, uint32_t hold)
{
    /* Break `first`, then the other beam `gap` us later; clear them in the same order. */
    uint8_t second = (uint8_t)(1u - first);
    CHECK(sm_beam_edge(b, first, true, t0) == SM_DIR_NONE);
    CHECK(sm_beam_edge(b, second, true, t0 + gap) == SM_DIR_NONE);
    CHECK(sm_beam_edge(b, first, false, t0 + hold) == SM_DIR_NONE);   /* still one beam blocked */
    return sm_beam_edge(b, second, false, t0 + hold + gap);
}

static void test_beams(void)
{
    sm_beam_t b;
    sm_beam_init(&b, 3000000u, 2000u);
    CHECK(walk(&b, 0, 1000, 120000, 400000) == SM_DIR_IN);      /* outer first */
    CHECK(walk(&b, 1, 5000000, 120000, 400000) == SM_DIR_OUT);  /* inner first */

    /* Stepped in and backed out: only the outer beam. */
    CHECK(sm_beam_edge(&b, 0, true, 9000000) == SM_DIR_NONE);
    CHECK(sm_beam_edge(&b, 0, false, 9300000) == SM_DIR_NONE);
    CHECK(b.aborted == 1);

    /* Both beams at once (a wide trolley): can't tell the direction. */
    CHECK(walk(&b, 0, 12000000, 500, 400000) == SM_DIR_NONE);
    CHECK(b.ambiguous == 1);

    /* Stood in the door for 5 s: not one crossing. */
    CHECK(walk(&b, 0, 20000000, 100000, 5000000) == SM_DIR_NONE);
    CHECK(b.aborted == 2);

    /* Repeated edges (noise) are ignored. */
    CHECK(sm_beam_edge(&b, 0, false, 30000000) == SM_DIR_NONE);

    /* Across the 71-minute microsecond wrap. */
    CHECK(walk(&b, 0, UINT32_MAX - 50000u, 120000, 400000) == SM_DIR_IN);

    /* Two people back to back: two counts. */
    CHECK(walk(&b, 0, 40000000, 100000, 300000) == SM_DIR_IN);
    CHECK(walk(&b, 0, 40600000, 100000, 300000) == SM_DIR_IN);
}

static void test_weight(void)
{
    sm_weight_t w;
    /* 420 counts per gram, offset 84000 (raw at zero load). */
    sm_weight_init(&w, 84000, 1, 420);
    int32_t g = 0;
    bool stable = false;
    uint32_t t = 0;

    bool due = sm_weight_push(&w, 84000 + 420 * 1840, t, false, &g, &stable);
    CHECK(due && g == 1840 && !stable);             /* first reading always sent, window not full */
    for (int i = 0; i < 7; i++) {
        t += 100;
        due = sm_weight_push(&w, 84000 + 420 * 1840 + (i % 2) * 420, t, false, &g, &stable);
    }
    CHECK(stable && g >= 1840 && g <= 1841);
    CHECK(due);                                     /* stable flag flipped -> send */
    t += 100;
    CHECK(!sm_weight_push(&w, 84000 + 420 * 1840, t, false, &g, &stable));  /* nothing new */

    /* A hand pressing: big swing -> unstable and sent. */
    t += 100;
    due = sm_weight_push(&w, 84000 + 420 * 2100, t, false, &g, &stable);
    CHECK(due && !stable);

    /* MEMS says the shelf is being handled: never stable. */
    for (int i = 0; i < 8; i++) {
        t += 100;
        sm_weight_push(&w, 84000 + 420 * 1622, t, true, &g, &stable);
    }
    CHECK(!stable && g == 1622);
    for (int i = 0; i < 8; i++) {
        t += 100;
        sm_weight_push(&w, 84000 + 420 * 1622, t, false, &g, &stable);
    }
    CHECK(stable && g == 1622);

    /* Periodic resend every 10 s even when nothing changes. */
    CHECK(!sm_weight_push(&w, 84000 + 420 * 1622, t + 5000u, false, &g, &stable));
    CHECK(sm_weight_push(&w, 84000 + 420 * 1622, t + 10000u, false, &g, &stable));

    /* Tare then calibrate with a 500 g weight. */
    sm_weight_t c;
    sm_weight_init(&c, 0, 1, 1);
    CHECK(!sm_weight_tare(&c));                     /* no samples yet */
    for (int i = 0; i < 8; i++) {
        sm_weight_push(&c, 123456, (uint32_t)i * 100u, false, &g, &stable);
    }
    CHECK(sm_weight_tare(&c) && c.offset == 123456);
    for (int i = 0; i < 8; i++) {
        sm_weight_push(&c, 123456 + 210000, 1000u + (uint32_t)i * 100u, false, &g, &stable);
    }
    CHECK(sm_weight_calibrate(&c, 500));
    sm_weight_push(&c, 123456 + 420 * 218, 3000, false, &g, &stable);
    CHECK(!sm_weight_calibrate(&c, 0));
    for (int i = 0; i < 8; i++) {
        sm_weight_push(&c, 123456 + 420 * 218, 4000u + (uint32_t)i * 100u, false, &g, &stable);
    }
    CHECK(g == 218);

    /* Inverted load cell wiring (counts go down with load) still works. */
    sm_weight_t inv;
    sm_weight_init(&inv, 0, 1, 1);
    for (int i = 0; i < 8; i++) {
        sm_weight_push(&inv, 1000, (uint32_t)i, false, &g, &stable);
    }
    sm_weight_tare(&inv);
    for (int i = 0; i < 8; i++) {
        sm_weight_push(&inv, 1000 - 42000, 10u + (uint32_t)i, false, &g, &stable);
    }
    CHECK(sm_weight_calibrate(&inv, 100));
    for (int i = 0; i < 8; i++) {
        sm_weight_push(&inv, 1000 - 42000, 20u + (uint32_t)i, false, &g, &stable);
    }
    CHECK(g == 100);
}

static void test_debounce(void)
{
    sm_deb_t d;
    sm_deb_init(&d, false, 50);
    CHECK(!sm_deb_update(&d, true, 1000));
    CHECK(!sm_deb_update(&d, false, 1020));       /* a 20 ms glitch */
    CHECK(!sm_deb_update(&d, true, 1030));
    CHECK(!sm_deb_update(&d, true, 1070));
    CHECK(sm_deb_update(&d, true, 1080) && d.state);
    CHECK(!sm_deb_update(&d, true, 2000));
}

int main(void)
{
    test_patterns();
    test_beams();
    test_weight();
    test_debounce();
    printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? 0 : 1;
}
