/*
 * sm_proto.h - StoreMind serial protocol, STM32 <-> Pi, demo (text) mode.
 *
 * docs/PROTOCOL.md is the single source of truth; this is its C half (the Python
 * half is storemind/storemind/sensors/protocol.py).  Both are tested against
 * every example line in PROTOCOL.md (tests/golden_vectors.h is generated from it).
 *
 * Frame:   $<T>,<seq>,<ms>,<field>,<field>...*<XX>\r\n
 *   T    one upper-case letter (message type)
 *   seq  rolling 0-255 counter, one per direction
 *   ms   sender uptime in ms (uint32, wraps after ~49 days)
 *   XX   XOR of every byte between '$' and '*', two upper-case hex digits
 *   at most 96 bytes including "\r\n"
 *
 * Portable C99: no HAL, no heap, no printf, no floats.  Every value on the wire
 * is an integer (newlib-nano printf has no float support); an empty field means
 * "not measured" and is represented here by SM_NONE.
 */
#ifndef SM_PROTO_H
#define SM_PROTO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SM_MAX_LINE   96u               /* bytes, including "\r\n" */
#define SM_MAX_FIELDS 16u               /* after <T>,<seq>,<ms> ($Q with 3 targets = 10) */
#define SM_MAX_ID     24u               /* longest id we keep ("shelf-a", "door1", ...) */
#define SM_NONE       INT32_MIN         /* empty field = not measured */

/* Why a line was dropped.  Names match ProtocolError.reason in protocol.py. */
typedef enum {
    SM_OK = 0,
    SM_ERR_FRAMING,
    SM_ERR_TOO_LONG,
    SM_ERR_CHECKSUM,
    SM_ERR_UNKNOWN_TYPE,
    SM_ERR_FIELDS
} sm_err_t;

const char *sm_err_name(sm_err_t err);   /* "framing", "checksum", ... */

/* ------------------------------------------------------------------------- */
/* Low level                                                                  */
/* ------------------------------------------------------------------------- */

uint8_t sm_xor(const char *body, size_t len);

/* A field as it appears on the wire (points into the caller's line buffer). */
typedef struct {
    const char *p;
    uint8_t len;
} sm_field_t;

typedef struct {
    char type;
    uint8_t seq;
    uint32_t ms;
    uint8_t nfields;
    bool too_many;              /* more than SM_MAX_FIELDS fields: never valid */
    sm_field_t f[SM_MAX_FIELDS];
} sm_frame_t;

/* Framing, length and checksum only; fields are not interpreted.  `line` may end
 * in "\r\n", "\n" or nothing.  `out` points into `line`, which must outlive it. */
sm_err_t sm_parse_frame(const char *line, size_t len, sm_frame_t *out);

/* Field helpers (strict: optional '-', digits only, range-checked). */
sm_err_t sm_field_int(const sm_field_t *f, int32_t *out);      /* empty -> error */
sm_err_t sm_field_opt(const sm_field_t *f, int32_t *out);      /* empty -> SM_NONE */
sm_err_t sm_field_u64(const sm_field_t *f, uint64_t *out);     /* epoch ms */
sm_err_t sm_field_bit(const sm_field_t *f, bool *out);         /* "0" / "1" */
sm_err_t sm_field_id(const sm_field_t *f, char *out, size_t cap); /* A-Za-z0-9 - _ . */
bool sm_field_is(const sm_field_t *f, const char *word);

/* ------------------------------------------------------------------------- */
/* Writer: builds one line field by field.  Any overflow or bad id poisons the */
/* writer and sm_w_end() returns 0, so a bad line is never sent half-built.    */
/* ------------------------------------------------------------------------- */

typedef struct {
    char buf[SM_MAX_LINE + 1];  /* +1 for a terminating NUL (not sent) */
    size_t len;
    bool bad;
} sm_writer_t;

void sm_w_begin(sm_writer_t *w, char type, uint8_t seq, uint32_t ms);
void sm_w_int(sm_writer_t *w, int32_t value);
void sm_w_opt(sm_writer_t *w, int32_t value);        /* SM_NONE -> empty field */
void sm_w_u64(sm_writer_t *w, uint64_t value);
void sm_w_id(sm_writer_t *w, const char *id);        /* validated id */
void sm_w_word(sm_writer_t *w, const char *word);    /* keyword: TOUCH, OK, IN... */
size_t sm_w_end(sm_writer_t *w);                     /* appends *XX\r\n; 0 = failed */

/* ------------------------------------------------------------------------- */
/* Typed messages (PROTOCOL.md section 3)                                     */
/* ------------------------------------------------------------------------- */

typedef enum { SM_PAT_OFF = 0, SM_PAT_ON, SM_PAT_SLOW, SM_PAT_FAST, SM_PAT_ALERT, SM_PAT_COUNT } sm_pattern_t;
typedef enum { SM_MEMS_TOUCH = 0, SM_MEMS_SETTLED, SM_MEMS_TILT, SM_MEMS_KNOCK, SM_MEMS_COUNT } sm_mems_event_t;
typedef enum {
    SM_RESET_POR = 0, SM_RESET_PIN, SM_RESET_IWDG, SM_RESET_WWDG, SM_RESET_SW,
    SM_RESET_BOR, SM_RESET_LPWR, SM_RESET_UNKNOWN, SM_RESET_COUNT
} sm_reset_t;
typedef enum { SM_CFG_TARE = 0, SM_CFG_CAL, SM_CFG_MEMS_THR, SM_CFG_MODE, SM_CFG_ENABLE, SM_CFG_COUNT } sm_cfg_key_t;

/* $K result codes */
enum {
    SM_K_OK = 0, SM_K_BAD_CHECKSUM = 1, SM_K_UNKNOWN_CMD = 2, SM_K_BAD_ARG = 3,
    SM_K_SENSOR_DISABLED = 4, SM_K_BUSY = 5
};

const char *sm_pattern_name(sm_pattern_t p);
const char *sm_mems_event_name(sm_mems_event_t e);
const char *sm_reset_name(sm_reset_t r);

/* Uplink encoders (MCU -> Pi).  Each writes one complete line into `out`
 * (capacity >= SM_MAX_LINE + 1, NUL-terminated) and returns its length, or 0
 * if it would not fit / an id is invalid. */
size_t sm_enc_weight(char *out, uint8_t seq, uint32_t ms, const char *slot, int32_t grams, bool stable);
size_t sm_enc_mems(char *out, uint8_t seq, uint32_t ms, const char *node, char role /* 'S'|'C' */,
                   sm_mems_event_t ev, int32_t peak_mg, int32_t rms_mg, int32_t dur_ms,
                   int32_t tilt_ddeg /* SM_NONE if not a tilt */);
size_t sm_enc_beam(char *out, uint8_t seq, uint32_t ms, const char *beam, bool clear);
size_t sm_enc_door(char *out, uint8_t seq, uint32_t ms, const char *door, bool in);
size_t sm_enc_presence(char *out, uint8_t seq, uint32_t ms, const char *zone, bool active);
size_t sm_enc_env(char *out, uint8_t seq, uint32_t ms, int32_t lux, int32_t temp_dc,
                  int32_t rh_dpct, int32_t hpa_d);       /* each may be SM_NONE */
size_t sm_enc_restock(char *out, uint8_t seq, uint32_t ms, const char *shelf);
size_t sm_enc_radar(char *out, uint8_t seq, uint32_t ms, uint8_t n, const int32_t *xyv /* 3*n */);
size_t sm_enc_health(char *out, uint8_t seq, uint32_t ms, uint32_t uptime_s, uint32_t free_heap,
                     uint32_t min_stack_words, uint32_t i2c_err, uint32_t uart_err, sm_reset_t cause);
size_t sm_enc_ack(char *out, uint8_t seq, uint32_t ms, uint8_t cmd_seq, bool ok, uint8_t code);

/* Downlink encoders (Pi -> MCU), used by host tests and tools. */
size_t sm_enc_sync(char *out, uint8_t seq, uint32_t ms, uint64_t epoch_ms);
size_t sm_enc_pattern(char *out, char type /* 'L'|'Z' */, uint8_t seq, uint32_t ms, sm_pattern_t p);
size_t sm_enc_servo(char *out, uint8_t seq, uint32_t ms, int32_t angle);

/* A decoded command (Pi -> MCU). */
typedef struct {
    char type;                  /* 'S', 'L', 'Z', 'V', 'C' */
    uint8_t seq;                /* echoed in $K */
    bool seq_valid;             /* false: the line was too broken to read seq, send no $K */
    uint64_t epoch_ms;          /* $S */
    sm_pattern_t pattern;       /* $L / $Z */
    int32_t angle;              /* $V, 0..180 */
    sm_cfg_key_t key;           /* $C */
    char id[SM_MAX_ID + 1];     /* $C TARE/CAL slot, MEMS_THR node, ENABLE sensor */
    int32_t value;              /* $C CAL grams, MEMS_THR mg, ENABLE 0/1 */
    bool bin_mode;              /* $C MODE: true = BIN */
} sm_cmd_t;

/* Parse + validate one command line.  On error, `*code` is the $K code to send
 * back (1 bad checksum, 2 unknown command, 3 bad argument) and `out->seq` is
 * filled whenever the frame itself was readable. */
sm_err_t sm_decode_cmd(const char *line, size_t len, sm_cmd_t *out, uint8_t *code);

/* A decoded uplink line (used by host tests and the HIL tool; the MCU does not
 * need it).  Numeric fields in order; ids copied. */
typedef struct {
    char type;
    uint8_t seq;
    uint32_t ms;
    char id[SM_MAX_ID + 1];     /* slot / node / beam / door / zone / shelf */
    char word[8];               /* role, event, IN/OUT, OK/ERR, reset cause */
    char word2[8];              /* $M event (word = role) */
    int32_t v[10];              /* numbers in wire order (SM_NONE when empty) */
    uint8_t nv;
} sm_up_t;

sm_err_t sm_decode_up(const char *line, size_t len, sm_up_t *out);

/* Decode any valid line, both directions (host tests: "every example parses"). */
sm_err_t sm_decode_any(const char *line, size_t len);

/* ------------------------------------------------------------------------- */
/* Stream helpers                                                              */
/* ------------------------------------------------------------------------- */

/* Byte-at-a-time line assembler (UART RX ISR / task).  Ignores bytes before
 * '$', drops a line with no terminator once it exceeds SM_MAX_LINE. */
typedef struct {
    char buf[SM_MAX_LINE + 1];
    size_t len;
    bool in_line;
    uint32_t dropped;
} sm_asm_t;

void sm_asm_init(sm_asm_t *a);
/* Returns the length of a complete line now in a->buf (NUL-terminated, with
 * its "\r\n" / "\n"), or 0.  The line stays valid until the next push. */
size_t sm_asm_push(sm_asm_t *a, uint8_t byte);

/* Lost-line counter from gaps in the rolling seq. */
typedef struct {
    bool have_last;
    uint8_t last;
    uint32_t lost;
} sm_seq_t;

void sm_seq_init(sm_seq_t *s);
uint8_t sm_seq_update(sm_seq_t *s, uint8_t seq);   /* returns the gap */

/* ------------------------------------------------------------------------- */
/* Production (binary) mode building blocks (PROTOCOL.md section 6).          */
/* The per-type struct layout is not final yet; these are the framing pieces. */
/* ------------------------------------------------------------------------- */

/* CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no final XOR. */
uint16_t sm_crc16(const uint8_t *data, size_t len);

/* COBS.  Encoded output never contains 0x00; the caller appends the 0x00
 * delimiter.  Worst case output = len + len/254 + 1.  Returns bytes written,
 * or 0 if `cap` is too small / the input is malformed. */
size_t sm_cobs_encode(const uint8_t *in, size_t len, uint8_t *out, size_t cap);
size_t sm_cobs_decode(const uint8_t *in, size_t len, uint8_t *out, size_t cap);

#ifdef __cplusplus
}
#endif

#endif /* SM_PROTO_H */
