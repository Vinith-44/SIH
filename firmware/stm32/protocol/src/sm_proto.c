/*
 * sm_proto.c - StoreMind serial protocol, demo (text) mode.  See sm_proto.h and
 * docs/PROTOCOL.md.  Portable C99: no HAL, no heap, no printf, no floats.
 */
#include "sm_proto.h"

#include <string.h>

/* ------------------------------------------------------------------------- */
/* Names                                                                      */
/* ------------------------------------------------------------------------- */

static const char *const ERR_NAMES[] = {
    "ok", "framing", "too_long", "checksum", "unknown_type", "fields"
};
static const char *const PATTERNS[SM_PAT_COUNT] = {"OFF", "ON", "SLOW", "FAST", "ALERT"};
static const char *const MEMS_EVENTS[SM_MEMS_COUNT] = {"TOUCH", "SETTLED", "TILT", "KNOCK"};
static const char *const RESETS[SM_RESET_COUNT] = {
    "POR", "PIN", "IWDG", "WWDG", "SW", "BOR", "LPWR", "UNKNOWN"
};
static const char *const CFG_KEYS[SM_CFG_COUNT] = {"TARE", "CAL", "MEMS_THR", "MODE", "ENABLE"};

const char *sm_err_name(sm_err_t err)
{
    return ((unsigned)err < sizeof ERR_NAMES / sizeof ERR_NAMES[0]) ? ERR_NAMES[err] : "?";
}

const char *sm_pattern_name(sm_pattern_t p)
{
    return ((unsigned)p < SM_PAT_COUNT) ? PATTERNS[p] : "OFF";
}

const char *sm_mems_event_name(sm_mems_event_t e)
{
    return ((unsigned)e < SM_MEMS_COUNT) ? MEMS_EVENTS[e] : "TOUCH";
}

const char *sm_reset_name(sm_reset_t r)
{
    return ((unsigned)r < SM_RESET_COUNT) ? RESETS[r] : "UNKNOWN";
}

/* ------------------------------------------------------------------------- */
/* Framing                                                                    */
/* ------------------------------------------------------------------------- */

uint8_t sm_xor(const char *body, size_t len)
{
    uint8_t x = 0;
    for (size_t i = 0; i < len; i++) {
        x ^= (uint8_t)body[i];
    }
    return x;
}

static int hex_upper(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

/* Strict decimal: optional '-', 1..19 digits.  Returns false on junk/overflow. */
static bool parse_i64(const char *p, size_t len, int64_t *out)
{
    size_t i = 0;
    bool neg = false;
    if (len > 0 && p[0] == '-') {
        neg = true;
        i = 1;
    }
    if (i >= len || len - i > 19) {
        return false;
    }
    uint64_t v = 0;
    for (; i < len; i++) {
        if (p[i] < '0' || p[i] > '9') {
            return false;
        }
        uint64_t digit = (uint64_t)(p[i] - '0');
        if (v > (UINT64_C(9223372036854775807) - digit) / 10u) {
            return false;
        }
        v = v * 10u + digit;
    }
    *out = neg ? -(int64_t)v : (int64_t)v;
    return true;
}

sm_err_t sm_parse_frame(const char *line, size_t len, sm_frame_t *out)
{
    memset(out, 0, sizeof *out);
    if (len > SM_MAX_LINE) {
        return SM_ERR_TOO_LONG;
    }
    while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
        len--;
    }
    if (len < 1 || line[0] != '$') {
        return SM_ERR_FRAMING;
    }
    size_t star = 0;
    unsigned stars = 0;
    for (size_t i = 0; i < len; i++) {
        uint8_t c = (uint8_t)line[i];
        if (c >= 0x80u) {
            return SM_ERR_FRAMING;          /* non-ASCII */
        }
        if (c == '$' && i != 0) {
            return SM_ERR_FRAMING;
        }
        if (c == '*') {
            stars++;
            star = i;
        }
    }
    if (stars != 1 || len - star - 1 != 2) {
        return SM_ERR_FRAMING;
    }
    int hi = hex_upper(line[star + 1]);
    int lo = hex_upper(line[star + 2]);
    if (hi < 0 || lo < 0) {
        return SM_ERR_FRAMING;
    }
    const char *body = line + 1;
    size_t body_len = star - 1;
    if ((uint8_t)((hi << 4) | lo) != sm_xor(body, body_len)) {
        return SM_ERR_CHECKSUM;
    }

    /* Split the body on ',' : <T>,<seq>,<ms>,fields... */
    sm_field_t parts[3];
    unsigned nparts = 0;
    size_t start = 0;
    for (size_t i = 0; i <= body_len; i++) {
        if (i == body_len || body[i] == ',') {
            sm_field_t f = {body + start, (uint8_t)(i - start)};
            if (nparts < 3) {
                parts[nparts] = f;
            } else if (out->nfields < SM_MAX_FIELDS) {
                out->f[out->nfields++] = f;
            } else {
                out->too_many = true;
            }
            nparts++;
            start = i + 1;
        }
    }
    if (nparts < 3 || parts[0].len != 1) {
        return SM_ERR_FRAMING;
    }
    int64_t seq;
    int64_t ms;
    if (!parse_i64(parts[1].p, parts[1].len, &seq) || !parse_i64(parts[2].p, parts[2].len, &ms)) {
        return SM_ERR_FRAMING;
    }
    if (seq < 0 || seq > 255 || ms < 0 || ms > (int64_t)UINT32_MAX) {
        return SM_ERR_FRAMING;
    }
    out->type = parts[0].p[0];
    out->seq = (uint8_t)seq;
    out->ms = (uint32_t)ms;
    return SM_OK;
}

/* ------------------------------------------------------------------------- */
/* Field helpers                                                              */
/* ------------------------------------------------------------------------- */

sm_err_t sm_field_int(const sm_field_t *f, int32_t *out)
{
    int64_t v;
    if (f->len == 0 || !parse_i64(f->p, f->len, &v) || v <= INT32_MIN || v > INT32_MAX) {
        return SM_ERR_FIELDS;   /* INT32_MIN is reserved for SM_NONE */
    }
    *out = (int32_t)v;
    return SM_OK;
}

sm_err_t sm_field_opt(const sm_field_t *f, int32_t *out)
{
    if (f->len == 0) {
        *out = SM_NONE;
        return SM_OK;
    }
    return sm_field_int(f, out);
}

sm_err_t sm_field_u64(const sm_field_t *f, uint64_t *out)
{
    int64_t v;
    if (f->len == 0 || !parse_i64(f->p, f->len, &v) || v < 0) {
        return SM_ERR_FIELDS;
    }
    *out = (uint64_t)v;
    return SM_OK;
}

sm_err_t sm_field_bit(const sm_field_t *f, bool *out)
{
    if (f->len != 1 || (f->p[0] != '0' && f->p[0] != '1')) {
        return SM_ERR_FIELDS;
    }
    *out = f->p[0] == '1';
    return SM_OK;
}

static bool id_char(char c)
{
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
           c == '-' || c == '_' || c == '.';
}

sm_err_t sm_field_id(const sm_field_t *f, char *out, size_t cap)
{
    if (f->len == 0 || (size_t)f->len >= cap) {
        return SM_ERR_FIELDS;
    }
    for (uint8_t i = 0; i < f->len; i++) {
        if (!id_char(f->p[i])) {
            return SM_ERR_FIELDS;
        }
        out[i] = f->p[i];
    }
    out[f->len] = '\0';
    return SM_OK;
}

bool sm_field_is(const sm_field_t *f, const char *word)
{
    size_t n = strlen(word);
    return n == f->len && memcmp(f->p, word, n) == 0;
}

static int field_index(const sm_field_t *f, const char *const *words, int count)
{
    for (int i = 0; i < count; i++) {
        if (sm_field_is(f, words[i])) {
            return i;
        }
    }
    return -1;
}

/* ------------------------------------------------------------------------- */
/* Writer                                                                     */
/* ------------------------------------------------------------------------- */

static void w_put(sm_writer_t *w, char c)
{
    /* Leave room for "*XX\r\n" so sm_w_end() can always finish the line. */
    if (w->bad || w->len + 1 + 5 > SM_MAX_LINE) {
        w->bad = true;
        return;
    }
    w->buf[w->len++] = c;
}

static void w_u64_digits(sm_writer_t *w, uint64_t v)
{
    char tmp[20];
    unsigned n = 0;
    do {
        tmp[n++] = (char)('0' + (int)(v % 10u));
        v /= 10u;
    } while (v != 0u && n < sizeof tmp);
    while (n > 0) {
        w_put(w, tmp[--n]);
    }
}

void sm_w_begin(sm_writer_t *w, char type, uint8_t seq, uint32_t ms)
{
    w->len = 0;
    w->bad = !(type >= 'A' && type <= 'Z');
    w->buf[w->len++] = '$';
    w_put(w, type);
    w_put(w, ',');
    w_u64_digits(w, seq);
    w_put(w, ',');
    w_u64_digits(w, ms);
}

void sm_w_int(sm_writer_t *w, int32_t value)
{
    w_put(w, ',');
    if (value < 0) {
        w_put(w, '-');
        w_u64_digits(w, (uint64_t)(-(int64_t)value));
    } else {
        w_u64_digits(w, (uint64_t)value);
    }
}

void sm_w_opt(sm_writer_t *w, int32_t value)
{
    if (value == SM_NONE) {
        w_put(w, ',');
    } else {
        sm_w_int(w, value);
    }
}

void sm_w_u64(sm_writer_t *w, uint64_t value)
{
    w_put(w, ',');
    w_u64_digits(w, value);
}

void sm_w_id(sm_writer_t *w, const char *id)
{
    size_t n = id ? strlen(id) : 0;
    if (n == 0 || n > SM_MAX_ID) {
        w->bad = true;
        return;
    }
    w_put(w, ',');
    for (size_t i = 0; i < n; i++) {
        if (!id_char(id[i])) {
            w->bad = true;
            return;
        }
        w_put(w, id[i]);
    }
}

void sm_w_word(sm_writer_t *w, const char *word)
{
    size_t n = word ? strlen(word) : 0;
    if (n == 0) {
        w->bad = true;
        return;
    }
    w_put(w, ',');
    for (size_t i = 0; i < n; i++) {
        char c = word[i];
        if (c == ',' || c == '*' || c == '$' || c < 0x21 || c > 0x7e) {
            w->bad = true;
            return;
        }
        w_put(w, c);
    }
}

size_t sm_w_end(sm_writer_t *w)
{
    static const char HEX[] = "0123456789ABCDEF";
    if (w->bad || w->len + 5 > SM_MAX_LINE) {
        w->bad = true;
        w->buf[0] = '\0';
        return 0;
    }
    uint8_t x = sm_xor(w->buf + 1, w->len - 1);
    w->buf[w->len++] = '*';
    w->buf[w->len++] = HEX[x >> 4];
    w->buf[w->len++] = HEX[x & 0x0Fu];
    w->buf[w->len++] = '\r';
    w->buf[w->len++] = '\n';
    w->buf[w->len] = '\0';
    return w->len;
}

static size_t finish(sm_writer_t *w, char *out)
{
    size_t n = sm_w_end(w);
    memcpy(out, w->buf, n + 1);   /* includes the NUL ("" on failure) */
    return n;
}

/* ------------------------------------------------------------------------- */
/* Typed encoders                                                             */
/* ------------------------------------------------------------------------- */

size_t sm_enc_weight(char *out, uint8_t seq, uint32_t ms, const char *slot, int32_t grams, bool stable)
{
    sm_writer_t w;
    sm_w_begin(&w, 'W', seq, ms);
    sm_w_id(&w, slot);
    sm_w_int(&w, grams);
    sm_w_int(&w, stable ? 1 : 0);
    return finish(&w, out);
}

size_t sm_enc_mems(char *out, uint8_t seq, uint32_t ms, const char *node, char role,
                   sm_mems_event_t ev, int32_t peak_mg, int32_t rms_mg, int32_t dur_ms,
                   int32_t tilt_ddeg)
{
    sm_writer_t w;
    char r[2] = {role, '\0'};
    sm_w_begin(&w, 'M', seq, ms);
    if ((role != 'S' && role != 'C') || (unsigned)ev >= SM_MEMS_COUNT) {
        w.bad = true;
    }
    sm_w_id(&w, node);
    sm_w_word(&w, r);
    sm_w_word(&w, sm_mems_event_name(ev));
    sm_w_int(&w, peak_mg);
    sm_w_int(&w, rms_mg);
    sm_w_int(&w, dur_ms);
    sm_w_opt(&w, tilt_ddeg);
    return finish(&w, out);
}

size_t sm_enc_beam(char *out, uint8_t seq, uint32_t ms, const char *beam, bool clear)
{
    sm_writer_t w;
    sm_w_begin(&w, 'B', seq, ms);
    sm_w_id(&w, beam);
    sm_w_int(&w, clear ? 1 : 0);
    return finish(&w, out);
}

size_t sm_enc_door(char *out, uint8_t seq, uint32_t ms, const char *door, bool in)
{
    sm_writer_t w;
    sm_w_begin(&w, 'D', seq, ms);
    sm_w_id(&w, door);
    sm_w_word(&w, in ? "IN" : "OUT");
    return finish(&w, out);
}

size_t sm_enc_presence(char *out, uint8_t seq, uint32_t ms, const char *zone, bool active)
{
    sm_writer_t w;
    sm_w_begin(&w, 'P', seq, ms);
    sm_w_id(&w, zone);
    sm_w_int(&w, active ? 1 : 0);
    return finish(&w, out);
}

size_t sm_enc_env(char *out, uint8_t seq, uint32_t ms, int32_t lux, int32_t temp_dc,
                  int32_t rh_dpct, int32_t hpa_d)
{
    sm_writer_t w;
    sm_w_begin(&w, 'E', seq, ms);
    sm_w_opt(&w, lux);
    sm_w_opt(&w, temp_dc);
    sm_w_opt(&w, rh_dpct);
    sm_w_opt(&w, hpa_d);
    return finish(&w, out);
}

size_t sm_enc_restock(char *out, uint8_t seq, uint32_t ms, const char *shelf)
{
    sm_writer_t w;
    sm_w_begin(&w, 'R', seq, ms);
    sm_w_id(&w, shelf);
    return finish(&w, out);
}

size_t sm_enc_radar(char *out, uint8_t seq, uint32_t ms, uint8_t n, const int32_t *xyv)
{
    sm_writer_t w;
    sm_w_begin(&w, 'Q', seq, ms);
    if (n > 3 || (n > 0 && xyv == NULL)) {
        w.bad = true;
    }
    sm_w_int(&w, n);
    for (unsigned i = 0; i < 3u * n && !w.bad; i++) {
        sm_w_int(&w, xyv[i]);
    }
    return finish(&w, out);
}

size_t sm_enc_health(char *out, uint8_t seq, uint32_t ms, uint32_t uptime_s, uint32_t free_heap,
                     uint32_t min_stack_words, uint32_t i2c_err, uint32_t uart_err, sm_reset_t cause)
{
    sm_writer_t w;
    sm_w_begin(&w, 'H', seq, ms);
    sm_w_u64(&w, uptime_s);
    sm_w_u64(&w, free_heap);
    sm_w_u64(&w, min_stack_words);
    sm_w_u64(&w, i2c_err);
    sm_w_u64(&w, uart_err);
    sm_w_word(&w, sm_reset_name(cause));
    return finish(&w, out);
}

size_t sm_enc_ack(char *out, uint8_t seq, uint32_t ms, uint8_t cmd_seq, bool ok, uint8_t code)
{
    sm_writer_t w;
    sm_w_begin(&w, 'K', seq, ms);
    sm_w_u64(&w, cmd_seq);
    sm_w_word(&w, ok ? "OK" : "ERR");
    sm_w_u64(&w, code);
    return finish(&w, out);
}

size_t sm_enc_sync(char *out, uint8_t seq, uint32_t ms, uint64_t epoch_ms)
{
    sm_writer_t w;
    sm_w_begin(&w, 'S', seq, ms);
    sm_w_u64(&w, epoch_ms);
    return finish(&w, out);
}

size_t sm_enc_pattern(char *out, char type, uint8_t seq, uint32_t ms, sm_pattern_t p)
{
    sm_writer_t w;
    sm_w_begin(&w, type, seq, ms);
    if ((type != 'L' && type != 'Z') || (unsigned)p >= SM_PAT_COUNT) {
        w.bad = true;
    }
    sm_w_word(&w, sm_pattern_name(p));
    return finish(&w, out);
}

size_t sm_enc_servo(char *out, uint8_t seq, uint32_t ms, int32_t angle)
{
    sm_writer_t w;
    sm_w_begin(&w, 'V', seq, ms);
    if (angle < 0 || angle > 180) {
        w.bad = true;
    }
    sm_w_int(&w, angle);
    return finish(&w, out);
}

/* ------------------------------------------------------------------------- */
/* Decoding                                                                   */
/* ------------------------------------------------------------------------- */

#define NEED(cond) do { if (!(cond)) { return SM_ERR_FIELDS; } } while (0)

static sm_err_t decode_cmd_frame(const sm_frame_t *fr, sm_cmd_t *out)
{
    const sm_field_t *f = fr->f;
    unsigned n = fr->nfields;
    int k;
    NEED(!fr->too_many);
    switch (fr->type) {
    case 'S':
        NEED(n == 1);
        return sm_field_u64(&f[0], &out->epoch_ms);
    case 'L':
    case 'Z':
        NEED(n == 1);
        k = field_index(&f[0], PATTERNS, SM_PAT_COUNT);
        NEED(k >= 0);
        out->pattern = (sm_pattern_t)k;
        return SM_OK;
    case 'V':
        NEED(n == 1);
        NEED(sm_field_int(&f[0], &out->angle) == SM_OK);
        NEED(out->angle >= 0 && out->angle <= 180);
        return SM_OK;
    case 'C':
        NEED(n >= 1);
        k = field_index(&f[0], CFG_KEYS, SM_CFG_COUNT);
        NEED(k >= 0);
        out->key = (sm_cfg_key_t)k;
        switch (out->key) {
        case SM_CFG_TARE:
            NEED(n == 2);
            return sm_field_id(&f[1], out->id, sizeof out->id);
        case SM_CFG_CAL:
        case SM_CFG_MEMS_THR:
            NEED(n == 3);
            NEED(sm_field_id(&f[1], out->id, sizeof out->id) == SM_OK);
            return sm_field_int(&f[2], &out->value);
        case SM_CFG_MODE:
            NEED(n == 2);
            NEED(sm_field_is(&f[1], "TXT") || sm_field_is(&f[1], "BIN"));
            out->bin_mode = sm_field_is(&f[1], "BIN");
            return SM_OK;
        case SM_CFG_ENABLE: {
            bool on;
            NEED(n == 3);
            NEED(sm_field_id(&f[1], out->id, sizeof out->id) == SM_OK);
            NEED(sm_field_bit(&f[2], &on) == SM_OK);
            out->value = on ? 1 : 0;
            return SM_OK;
        }
        default:
            return SM_ERR_FIELDS;
        }
    default:
        return SM_ERR_UNKNOWN_TYPE;
    }
}

/* Best effort: read <seq> from a line whose checksum is wrong, so the MCU can
 * still answer "$K,<seq>,ERR,1". */
static bool seq_despite_checksum(const char *line, size_t len, uint8_t *seq)
{
    size_t i = 0;
    while (i < len && line[i] != ',') {
        i++;
    }
    size_t start = ++i;
    while (i < len && line[i] != ',') {
        i++;
    }
    int64_t v;
    if (i >= len || !parse_i64(line + start, i - start, &v) || v < 0 || v > 255) {
        return false;
    }
    *seq = (uint8_t)v;
    return true;
}

sm_err_t sm_decode_cmd(const char *line, size_t len, sm_cmd_t *out, uint8_t *code)
{
    sm_frame_t fr;
    memset(out, 0, sizeof *out);
    sm_err_t err = sm_parse_frame(line, len, &fr);
    if (err == SM_ERR_CHECKSUM) {
        out->seq_valid = seq_despite_checksum(line, len, &out->seq);
        *code = SM_K_BAD_CHECKSUM;
        return err;
    }
    if (err != SM_OK) {
        *code = SM_K_BAD_CHECKSUM;       /* unreadable: no $K is sent (seq_valid false) */
        return err;
    }
    out->type = fr.type;
    out->seq = fr.seq;
    out->seq_valid = true;
    err = decode_cmd_frame(&fr, out);
    *code = err == SM_OK ? SM_K_OK : (err == SM_ERR_UNKNOWN_TYPE ? SM_K_UNKNOWN_CMD : SM_K_BAD_ARG);
    return err;
}

static sm_err_t up_ints(const sm_frame_t *fr, unsigned from, unsigned count, bool optional, sm_up_t *out)
{
    for (unsigned i = 0; i < count; i++) {
        int32_t v;
        sm_err_t e = optional ? sm_field_opt(&fr->f[from + i], &v) : sm_field_int(&fr->f[from + i], &v);
        NEED(e == SM_OK && out->nv < sizeof out->v / sizeof out->v[0]);
        out->v[out->nv++] = v;
    }
    return SM_OK;
}

static sm_err_t up_word(const sm_field_t *f, const char *const *words, int count, char *dst, size_t cap)
{
    NEED(field_index(f, words, count) >= 0 && (size_t)f->len < cap);
    memcpy(dst, f->p, f->len);
    dst[f->len] = '\0';
    return SM_OK;
}

static sm_err_t up_bit(const sm_field_t *f, sm_up_t *out)
{
    bool b;
    NEED(sm_field_bit(f, &b) == SM_OK);
    out->v[out->nv++] = b ? 1 : 0;
    return SM_OK;
}

static const char *const ROLES[] = {"S", "C"};
static const char *const DIRS[] = {"IN", "OUT"};
static const char *const STATUS[] = {"OK", "ERR"};

sm_err_t sm_decode_up(const char *line, size_t len, sm_up_t *out)
{
    sm_frame_t fr;
    memset(out, 0, sizeof *out);
    sm_err_t err = sm_parse_frame(line, len, &fr);
    if (err != SM_OK) {
        return err;
    }
    out->type = fr.type;
    out->seq = fr.seq;
    out->ms = fr.ms;
    const sm_field_t *f = fr.f;
    unsigned n = fr.nfields;
    if (strchr("WMBDPERQHK", fr.type) == NULL || fr.type == '\0') {
        return SM_ERR_UNKNOWN_TYPE;
    }
    NEED(!fr.too_many);
    switch (fr.type) {
    case 'W':
        NEED(n == 3);
        NEED(sm_field_id(&f[0], out->id, sizeof out->id) == SM_OK);
        NEED(up_ints(&fr, 1, 1, false, out) == SM_OK);
        return up_bit(&f[2], out);
    case 'M':
        NEED(n == 7);
        NEED(sm_field_id(&f[0], out->id, sizeof out->id) == SM_OK);
        NEED(up_word(&f[1], ROLES, 2, out->word, sizeof out->word) == SM_OK);
        NEED(up_word(&f[2], MEMS_EVENTS, SM_MEMS_COUNT, out->word2, sizeof out->word2) == SM_OK);
        NEED(up_ints(&fr, 3, 3, false, out) == SM_OK);
        return up_ints(&fr, 6, 1, true, out);
    case 'B':
    case 'P':
        NEED(n == 2);
        NEED(sm_field_id(&f[0], out->id, sizeof out->id) == SM_OK);
        return up_bit(&f[1], out);
    case 'D':
        NEED(n == 2);
        NEED(sm_field_id(&f[0], out->id, sizeof out->id) == SM_OK);
        return up_word(&f[1], DIRS, 2, out->word, sizeof out->word);
    case 'E':
        NEED(n == 4);
        return up_ints(&fr, 0, 4, true, out);
    case 'R':
        NEED(n == 1);
        return sm_field_id(&f[0], out->id, sizeof out->id);
    case 'Q': {
        NEED(n >= 1);
        NEED(up_ints(&fr, 0, 1, false, out) == SM_OK);
        int32_t targets = out->v[0];
        NEED(targets >= 0 && targets <= 3 && n == 1u + 3u * (unsigned)targets);
        return up_ints(&fr, 1, 3u * (unsigned)targets, false, out);
    }
    case 'H':
        NEED(n == 6);
        NEED(up_ints(&fr, 0, 5, false, out) == SM_OK);
        return up_word(&f[5], RESETS, SM_RESET_COUNT, out->word, sizeof out->word);
    case 'K':
        NEED(n == 3);
        NEED(up_ints(&fr, 0, 1, false, out) == SM_OK);
        NEED(up_word(&f[1], STATUS, 2, out->word, sizeof out->word) == SM_OK);
        return up_ints(&fr, 2, 1, false, out);
    default:
        return SM_ERR_UNKNOWN_TYPE;
    }
}

sm_err_t sm_decode_any(const char *line, size_t len)
{
    sm_frame_t fr;
    sm_err_t err = sm_parse_frame(line, len, &fr);
    if (err != SM_OK) {
        return err;
    }
    if (fr.type != '\0' && strchr("SLZVC", fr.type) != NULL) {
        sm_cmd_t cmd;
        memset(&cmd, 0, sizeof cmd);
        return decode_cmd_frame(&fr, &cmd);
    }
    sm_up_t up;
    return sm_decode_up(line, len, &up);
}

/* ------------------------------------------------------------------------- */
/* Stream helpers                                                             */
/* ------------------------------------------------------------------------- */

void sm_asm_init(sm_asm_t *a)
{
    memset(a, 0, sizeof *a);
}

size_t sm_asm_push(sm_asm_t *a, uint8_t byte)
{
    if (byte == '$') {                 /* a '$' always starts a fresh line */
        if (a->in_line) {
            a->dropped++;              /* the previous line never ended */
        }
        a->buf[0] = '$';
        a->len = 1;
        a->in_line = true;
        return 0;
    }
    if (!a->in_line) {
        return 0;                      /* noise before '$' */
    }
    if (a->len >= SM_MAX_LINE) {       /* runaway line with no terminator */
        a->in_line = false;
        a->len = 0;
        a->dropped++;
        return 0;
    }
    a->buf[a->len++] = (char)byte;
    if (byte == '\n') {
        size_t n = a->len;
        a->buf[n] = '\0';
        a->in_line = false;
        a->len = 0;
        return n;
    }
    return 0;
}

void sm_seq_init(sm_seq_t *s)
{
    memset(s, 0, sizeof *s);
}

uint8_t sm_seq_update(sm_seq_t *s, uint8_t seq)
{
    uint8_t gap = 0;
    if (s->have_last) {
        gap = (uint8_t)(seq - s->last - 1u);
        s->lost += gap;
    }
    s->have_last = true;
    s->last = seq;
    return gap;
}
