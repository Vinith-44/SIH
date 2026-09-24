/*
 * sm_logic.h - the sensor node's decision logic, as portable C99.
 *
 * Everything that decides something (is the weight stable? which way did the
 * person walk? is the buzzer on right now?) lives here, with no HAL calls, so
 * it compiles and is unit-tested on the PC (tests/test_sm_logic.c) exactly as it
 * runs on the STM32.  The firmware tasks only move bytes between the hardware
 * and these functions.
 *
 * Integer-only: no float on the Cortex-M3 (no FPU) and none on the wire.
 */
#ifndef SM_LOGIC_H
#define SM_LOGIC_H

#include <stdbool.h>
#include <stdint.h>

#include "sm_proto.h"   /* sm_pattern_t */

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------------- */
/* LED / buzzer patterns (non-blocking: ask "on or off at time t?")           */
/*   OFF, ON (steady), SLOW 1 Hz, FAST 4 Hz,                                  */
/*   ALERT = three 100 ms pulses then 400 ms off, repeating every second.     */
/* ------------------------------------------------------------------------- */

typedef struct {
    sm_pattern_t pattern;
    uint32_t start_ms;
    uint32_t auto_off_ms;   /* 0 = never; else the pattern turns OFF after this */
} sm_pat_t;

void sm_pat_set(sm_pat_t *p, sm_pattern_t pattern, uint32_t now_ms, uint32_t auto_off_ms);
bool sm_pat_output(sm_pat_t *p, uint32_t now_ms);

/* ------------------------------------------------------------------------- */
/* Two IR break-beams across a door -> IN / OUT.                              */
/* Beam 0 = outer (street side), beam 1 = inner.  A crossing counts only when */
/* both beams were broken and are clear again, within max_transit_us; the     */
/* beam broken first gives the direction.  Stepping in and back out (only one */
/* beam) or both beams breaking within min_gap_us (can't tell) counts nothing. */
/* ------------------------------------------------------------------------- */

typedef enum { SM_DIR_NONE = 0, SM_DIR_IN, SM_DIR_OUT } sm_dir_t;

typedef struct {
    uint32_t max_transit_us;   /* default 3 s: slower than this is not one crossing */
    uint32_t min_gap_us;       /* default 2 ms: closer than this = ambiguous */
    bool blocked[2];
    bool seen[2];
    uint32_t t_block[2];
    int8_t first;              /* -1 = idle */
    uint32_t ambiguous;        /* counters for NODE_HEALTH / debugging */
    uint32_t aborted;
} sm_beam_t;

void sm_beam_init(sm_beam_t *b, uint32_t max_transit_us, uint32_t min_gap_us);
sm_dir_t sm_beam_edge(sm_beam_t *b, uint8_t beam, bool blocked, uint32_t t_us);

/* ------------------------------------------------------------------------- */
/* Load cell: raw HX711 counts -> grams, stability, and when to send $W.     */
/* grams = (mean_raw - offset) * scale_num / scale_den                        */
/* ------------------------------------------------------------------------- */

#define SM_WEIGHT_WINDOW 8u

typedef struct {
    int32_t offset;            /* raw counts at zero load (tare) */
    int32_t scale_num;         /* grams per count as a fraction */
    int32_t scale_den;
    int32_t stable_band_g;     /* max-min over the window for "stable" (default 5 g) */
    int32_t change_g;          /* send when the reading moves more than this (5 g) */
    uint32_t period_ms;        /* ... and at least this often (10 s) */
    int32_t raw[SM_WEIGHT_WINDOW];
    uint8_t n;
    uint8_t idx;
    bool have_sent;
    int32_t sent_g;
    bool sent_stable;
    uint32_t sent_ms;
} sm_weight_t;

void sm_weight_init(sm_weight_t *w, int32_t offset, int32_t scale_num, int32_t scale_den);
/* Feed one raw sample.  Fills grams/stable; returns true when a $W is due.
 * force_unstable: the shelf is being handled (MEMS ACTIVE), never "stable". */
bool sm_weight_push(sm_weight_t *w, int32_t raw, uint32_t now_ms, bool force_unstable,
                    int32_t *grams, bool *stable);
int32_t sm_weight_mean_raw(const sm_weight_t *w);
bool sm_weight_tare(sm_weight_t *w);                        /* false until the window is full */
bool sm_weight_calibrate(sm_weight_t *w, int32_t known_g);  /* false if no load / bad value */

/* ------------------------------------------------------------------------- */
/* Debounce (PIR output, restock button)                                      */
/* ------------------------------------------------------------------------- */

typedef struct {
    bool state;
    bool candidate;
    uint32_t since_ms;
    uint32_t hold_ms;
} sm_deb_t;

void sm_deb_init(sm_deb_t *d, bool initial, uint32_t hold_ms);
/* Returns true when the debounced state changes. */
bool sm_deb_update(sm_deb_t *d, bool level, uint32_t now_ms);

#ifdef __cplusplus
}
#endif

#endif /* SM_LOGIC_H */
