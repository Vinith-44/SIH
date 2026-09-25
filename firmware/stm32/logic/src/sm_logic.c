/*
 * sm_logic.c - patterns, beam direction, load-cell filter, debounce.
 * See sm_logic.h.  Portable C99, integer-only, no HAL.
 */
#include "sm_logic.h"

#include <string.h>

/* ------------------------------------------------------------------------- */
/* Patterns                                                                   */
/* ------------------------------------------------------------------------- */

void sm_pat_set(sm_pat_t *p, sm_pattern_t pattern, uint32_t now_ms, uint32_t auto_off_ms)
{
    p->pattern = pattern;
    p->start_ms = now_ms;
    p->auto_off_ms = auto_off_ms;
}

bool sm_pat_output(sm_pat_t *p, uint32_t now_ms)
{
    uint32_t t = now_ms - p->start_ms;       /* wraps correctly */
    if (p->auto_off_ms != 0u && p->pattern != SM_PAT_OFF && t >= p->auto_off_ms) {
        p->pattern = SM_PAT_OFF;
    }
    switch (p->pattern) {
    case SM_PAT_ON:
        return true;
    case SM_PAT_SLOW:
        return (t % 1000u) < 500u;
    case SM_PAT_FAST:
        return (t % 250u) < 125u;
    case SM_PAT_ALERT: {
        uint32_t phase = t % 1000u;
        return phase < 600u && (phase % 200u) < 100u;
    }
    case SM_PAT_OFF:
    default:
        return false;
    }
}

/* ------------------------------------------------------------------------- */
/* Beams                                                                      */
/* ------------------------------------------------------------------------- */

void sm_beam_init(sm_beam_t *b, uint32_t max_transit_us, uint32_t min_gap_us)
{
    memset(b, 0, sizeof *b);
    b->max_transit_us = max_transit_us ? max_transit_us : 3000000u;
    b->min_gap_us = min_gap_us;
    b->first = -1;
}

static void beam_reset(sm_beam_t *b)
{
    b->seen[0] = b->seen[1] = false;
    b->first = -1;
}

sm_dir_t sm_beam_edge(sm_beam_t *b, uint8_t beam, bool blocked, uint32_t t_us)
{
    if (beam > 1u || b->blocked[beam] == blocked) {
        return SM_DIR_NONE;                  /* not a real edge */
    }
    b->blocked[beam] = blocked;
    if (blocked) {
        if (b->first < 0) {
            b->first = (int8_t)beam;
        }
        b->seen[beam] = true;
        b->t_block[beam] = t_us;
        return SM_DIR_NONE;
    }
    if (b->blocked[0] || b->blocked[1]) {
        return SM_DIR_NONE;                  /* still someone in the doorway */
    }
    /* Both clear: decide. */
    sm_dir_t dir = SM_DIR_NONE;
    if (b->first >= 0 && b->seen[0] && b->seen[1]) {
        uint8_t first = (uint8_t)b->first;
        uint8_t second = (uint8_t)(1u - first);
        uint32_t gap = b->t_block[second] - b->t_block[first];
        uint32_t transit = t_us - b->t_block[first];
        if (gap < b->min_gap_us) {
            b->ambiguous++;
        } else if (transit > b->max_transit_us) {
            b->aborted++;
        } else {
            dir = first == 0u ? SM_DIR_IN : SM_DIR_OUT;
        }
    } else if (b->first >= 0) {
        b->aborted++;                         /* only one beam: stepped in and back */
    }
    beam_reset(b);
    return dir;
}

/* ------------------------------------------------------------------------- */
/* Load cell                                                                  */
/* ------------------------------------------------------------------------- */

void sm_weight_init(sm_weight_t *w, int32_t offset, int32_t scale_num, int32_t scale_den)
{
    memset(w, 0, sizeof *w);
    w->offset = offset;
    w->scale_num = scale_num;
    w->scale_den = scale_den > 0 ? scale_den : 1;
    w->stable_band_g = 5;
    w->change_g = 5;
    w->period_ms = 10000u;
}

int32_t sm_weight_mean_raw(const sm_weight_t *w)
{
    if (w->n == 0u) {
        return 0;
    }
    int64_t sum = 0;
    for (uint8_t i = 0; i < w->n; i++) {
        sum += w->raw[i];
    }
    return (int32_t)(sum / (int64_t)w->n);
}

static int32_t to_grams(const sm_weight_t *w, int32_t raw)
{
    int64_t g = ((int64_t)raw - (int64_t)w->offset) * (int64_t)w->scale_num;
    g /= (int64_t)w->scale_den;
    if (g > INT32_MAX) {
        g = INT32_MAX;
    } else if (g < -INT32_MAX) {
        g = -INT32_MAX;
    }
    return (int32_t)g;
}

bool sm_weight_push(sm_weight_t *w, int32_t raw, uint32_t now_ms, bool force_unstable,
                    int32_t *grams, bool *stable)
{
    w->raw[w->idx] = raw;
    w->idx = (uint8_t)((w->idx + 1u) % SM_WEIGHT_WINDOW);
    if (w->n < SM_WEIGHT_WINDOW) {
        w->n++;
    }
    int32_t lo = INT32_MAX;
    int32_t hi = INT32_MIN;
    for (uint8_t i = 0; i < w->n; i++) {
        int32_t g = to_grams(w, w->raw[i]);
        lo = g < lo ? g : lo;
        hi = g > hi ? g : hi;
    }
    *grams = to_grams(w, sm_weight_mean_raw(w));
    *stable = !force_unstable && w->n == SM_WEIGHT_WINDOW && (hi - lo) <= w->stable_band_g;

    int32_t moved = *grams - w->sent_g;
    if (moved < 0) {
        moved = -moved;
    }
    bool due = !w->have_sent || moved > w->change_g || *stable != w->sent_stable ||
               (now_ms - w->sent_ms) >= w->period_ms;
    if (due) {
        w->have_sent = true;
        w->sent_g = *grams;
        w->sent_stable = *stable;
        w->sent_ms = now_ms;
    }
    return due;
}

bool sm_weight_tare(sm_weight_t *w)
{
    if (w->n < SM_WEIGHT_WINDOW) {
        return false;
    }
    w->offset = sm_weight_mean_raw(w);
    w->have_sent = false;                    /* report the new zero right away */
    return true;
}

bool sm_weight_calibrate(sm_weight_t *w, int32_t known_g)
{
    if (w->n < SM_WEIGHT_WINDOW || known_g <= 0) {
        return false;
    }
    int32_t counts = sm_weight_mean_raw(w) - w->offset;
    if (counts == 0) {
        return false;
    }
    /* grams per count = known_g / counts, kept as a fraction (no float). */
    w->scale_num = known_g;
    w->scale_den = counts;
    if (w->scale_den < 0) {
        w->scale_den = -w->scale_den;
        w->scale_num = -w->scale_num;
    }
    w->have_sent = false;
    return true;
}

/* ------------------------------------------------------------------------- */
/* Debounce                                                                   */
/* ------------------------------------------------------------------------- */

void sm_deb_init(sm_deb_t *d, bool initial, uint32_t hold_ms)
{
    d->state = initial;
    d->candidate = initial;
    d->since_ms = 0;
    d->hold_ms = hold_ms;
}

bool sm_deb_update(sm_deb_t *d, bool level, uint32_t now_ms)
{
    if (level != d->candidate) {
        d->candidate = level;
        d->since_ms = now_ms;
    }
    if (d->candidate != d->state && (now_ms - d->since_ms) >= d->hold_ms) {
        d->state = d->candidate;
        return true;
    }
    return false;
}
