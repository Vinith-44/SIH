/*
 * Host unit tests for the portable protocol library (runs on the PC and in CI).
 *
 *   cmake -S firmware/stm32/protocol -B build/proto && cmake --build build/proto
 *   ctest --test-dir build/proto --output-on-failure
 *
 * No test framework: a tiny CHECK macro keeps this buildable with any C99
 * compiler, including the one CI installs.
 */
#include <stdio.h>
#include <string.h>

#include "golden_vectors.h"
#include "sm_proto.h"

static int g_checks;
static int g_failures;

#define CHECK(cond) do { \
    g_checks++; \
    if (!(cond)) { g_failures++; fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); } \
} while (0)

#define CHECK_STR(a, b) do { \
    g_checks++; \
    if (strcmp((a), (b)) != 0) { g_failures++; \
        fprintf(stderr, "%s:%d: expected \"%s\", got \"%s\"\n", __FILE__, __LINE__, (b), (a)); } \
} while (0)

#define COUNT(a) (sizeof(a) / sizeof((a)[0]))

/* Rebuild a line from its parsed frame with the generic writer. */
static size_t reencode(const sm_frame_t *fr, char *out)
{
    sm_writer_t w;
    sm_w_begin(&w, fr->type, fr->seq, fr->ms);
    for (unsigned i = 0; i < fr->nfields; i++) {
        char tmp[SM_MAX_LINE + 1];
        memcpy(tmp, fr->f[i].p, fr->f[i].len);
        tmp[fr->f[i].len] = '\0';
        if (fr->f[i].len == 0) {
            sm_w_opt(&w, SM_NONE);
        } else {
            sm_w_word(&w, tmp);
        }
    }
    size_t n = sm_w_end(&w);
    memcpy(out, w.buf, n + 1);
    return n;
}

static void test_golden_valid(void)
{
    for (size_t i = 0; i < COUNT(GOLDEN_VALID); i++) {
        const char *line = GOLDEN_VALID[i];
        char with_crlf[SM_MAX_LINE + 3];
        snprintf(with_crlf, sizeof with_crlf, "%s\r\n", line);

        sm_err_t err = sm_decode_any(with_crlf, strlen(with_crlf));
        if (err != SM_OK) {
            fprintf(stderr, "valid example rejected (%s): %s\n", sm_err_name(err), line);
        }
        CHECK(err == SM_OK);

        sm_frame_t fr;
        CHECK(sm_parse_frame(line, strlen(line), &fr) == SM_OK);
        char again[SM_MAX_LINE + 1];
        CHECK(reencode(&fr, again) > 0);
        CHECK_STR(again, with_crlf);
    }
}

static void test_golden_invalid(void)
{
    for (size_t i = 0; i < COUNT(GOLDEN_INVALID); i++) {
        const char *line = GOLDEN_INVALID[i].line;
        sm_err_t err = sm_decode_any(line, strlen(line));
        if (strcmp(sm_err_name(err), GOLDEN_INVALID[i].reason) != 0) {
            fprintf(stderr, "invalid example %s: expected %s, got %s\n",
                    line, GOLDEN_INVALID[i].reason, sm_err_name(err));
        }
        CHECK_STR(sm_err_name(err), GOLDEN_INVALID[i].reason);
    }
}

/* The typed encoders must produce exactly the doc's example lines. */
static void test_typed_encoders_match_doc(void)
{
    char out[SM_MAX_LINE + 1];
    const int32_t radar[] = {-320, 1450, 0, 410, 2210, -12};

    sm_enc_weight(out, 17, 523040, "A1", 1840, true);
    CHECK_STR(out, "$W,17,523040,A1,1840,1*31\r\n");
    sm_enc_mems(out, 18, 523610, "m1", 'S', SM_MEMS_TOUCH, 412, 138, 640, SM_NONE);
    CHECK_STR(out, "$M,18,523610,m1,S,TOUCH,412,138,640,*1E\r\n");
    sm_enc_mems(out, 21, 530002, "m1", 'S', SM_MEMS_TILT, 90, 40, 2500, 62);
    CHECK_STR(out, "$M,21,530002,m1,S,TILT,90,40,2500,62*62\r\n");
    sm_enc_mems(out, 22, 601400, "m2", 'C', SM_MEMS_KNOCK, 1850, 420, 40, SM_NONE);
    CHECK_STR(out, "$M,22,601400,m2,C,KNOCK,1850,420,40,*02\r\n");
    sm_enc_beam(out, 23, 610001, "b1", false);
    CHECK_STR(out, "$B,23,610001,b1,0*26\r\n");
    sm_enc_door(out, 24, 610140, "door1", true);
    CHECK_STR(out, "$D,24,610140,door1,IN*60\r\n");
    sm_enc_presence(out, 25, 611000, "aisle1", true);
    CHECK_STR(out, "$P,25,611000,aisle1,1*23\r\n");
    sm_enc_env(out, 26, 612000, 420, 284, 615, 10093);
    CHECK_STR(out, "$E,26,612000,420,284,615,10093*45\r\n");
    sm_enc_env(out, 27, 672000, 3, SM_NONE, SM_NONE, SM_NONE);
    CHECK_STR(out, "$E,27,672000,3,,,*70\r\n");
    sm_enc_restock(out, 28, 700000, "shelf-a");
    CHECK_STR(out, "$R,28,700000,shelf-a*4B\r\n");
    sm_enc_radar(out, 29, 700100, 2, radar);
    CHECK_STR(out, "$Q,29,700100,2,-320,1450,0,410,2210,-12*74\r\n");
    sm_enc_radar(out, 30, 700200, 0, NULL);
    CHECK_STR(out, "$Q,30,700200,0*4B\r\n");
    sm_enc_health(out, 31, 720000, 720, 6144, 38, 0, 0, SM_RESET_POR);
    CHECK_STR(out, "$H,31,720000,720,6144,38,0,0,POR*3B\r\n");
    sm_enc_ack(out, 32, 720050, 5, true, 0);
    CHECK_STR(out, "$K,32,720050,5,OK,0*67\r\n");
    sm_enc_ack(out, 33, 720300, 6, false, 3);
    CHECK_STR(out, "$K,33,720300,6,ERR,3*21\r\n");

    sm_enc_sync(out, 5, 3600100, UINT64_C(1790239200000));
    CHECK_STR(out, "$S,5,3600100,1790239200000*4B\r\n");
    sm_enc_pattern(out, 'L', 6, 3600250, SM_PAT_ALERT);
    CHECK_STR(out, "$L,6,3600250,ALERT*2A\r\n");
    sm_enc_pattern(out, 'Z', 7, 3600260, SM_PAT_SLOW);
    CHECK_STR(out, "$Z,7,3600260,SLOW*77\r\n");
    sm_enc_servo(out, 8, 3600300, 90);
    CHECK_STR(out, "$V,8,3600300,90*7D\r\n");
}

static void test_encoders_refuse_bad_input(void)
{
    char out[SM_MAX_LINE + 1];
    char long_id[40];
    memset(long_id, 'x', sizeof long_id - 1);
    long_id[sizeof long_id - 1] = '\0';

    CHECK(sm_enc_weight(out, 1, 1, "A 1", 5, true) == 0);        /* space in id */
    CHECK(out[0] == '\0');
    CHECK(sm_enc_weight(out, 1, 1, "", 5, true) == 0);
    CHECK(sm_enc_restock(out, 1, 1, long_id) == 0);              /* id too long */
    CHECK(sm_enc_servo(out, 1, 1, 181) == 0);
    CHECK(sm_enc_servo(out, 1, 1, -1) == 0);
    CHECK(sm_enc_pattern(out, 'X', 1, 1, SM_PAT_ON) == 0);
    CHECK(sm_enc_mems(out, 1, 1, "m1", 'Q', SM_MEMS_TOUCH, 1, 1, 1, SM_NONE) == 0);
    CHECK(sm_enc_radar(out, 1, 1, 4, NULL) == 0);

    /* Never more than 96 bytes: a line that would overflow is refused whole. */
    sm_writer_t w;
    sm_w_begin(&w, 'R', 255, UINT32_MAX);
    for (int i = 0; i < 20; i++) {
        sm_w_int(&w, -2000000000);
    }
    CHECK(sm_w_end(&w) == 0);

    /* The largest values still fit in 96 bytes.  Decoders read numeric fields as
     * int32, so counters must stay below 2^31 (68 years of uptime_s). */
    size_t n = sm_enc_health(out, 255, UINT32_MAX, UINT32_MAX, 0, 0, 0, 0, SM_RESET_UNKNOWN);
    CHECK(n > 0 && n <= SM_MAX_LINE);
    CHECK(sm_decode_any(out, n) == SM_ERR_FIELDS);     /* uptime 2^32-1 is out of range */
    n = sm_enc_health(out, 255, UINT32_MAX, (uint32_t)INT32_MAX, (uint32_t)INT32_MAX,
                      (uint32_t)INT32_MAX, (uint32_t)INT32_MAX, (uint32_t)INT32_MAX, SM_RESET_UNKNOWN);
    CHECK(n > 0 && n <= SM_MAX_LINE);
    CHECK(sm_decode_any(out, n) == SM_OK);
}

static void test_command_decode(void)
{
    sm_cmd_t c;
    uint8_t code = 99;
    const char *l;

    l = "$S,5,3600100,1790239200000*4B\r\n";
    CHECK(sm_decode_cmd(l, strlen(l), &c, &code) == SM_OK);
    CHECK(c.type == 'S' && c.seq == 5 && c.epoch_ms == UINT64_C(1790239200000) && code == SM_K_OK);

    l = "$C,10,3600500,CAL,A1,500*55";
    CHECK(sm_decode_cmd(l, strlen(l), &c, &code) == SM_OK);
    CHECK(c.key == SM_CFG_CAL && strcmp(c.id, "A1") == 0 && c.value == 500);

    l = "$C,11,3600600,MEMS_THR,m1,120*34";
    CHECK(sm_decode_cmd(l, strlen(l), &c, &code) == SM_OK);
    CHECK(c.key == SM_CFG_MEMS_THR && strcmp(c.id, "m1") == 0 && c.value == 120);

    l = "$C,12,3600700,MODE,BIN*34";
    CHECK(sm_decode_cmd(l, strlen(l), &c, &code) == SM_OK);
    CHECK(c.key == SM_CFG_MODE && c.bin_mode);

    l = "$C,13,3600800,ENABLE,bme280,0*31";
    CHECK(sm_decode_cmd(l, strlen(l), &c, &code) == SM_OK);
    CHECK(c.key == SM_CFG_ENABLE && strcmp(c.id, "bme280") == 0 && c.value == 0);

    /* Bad checksum: still answer with the seq so the Pi retries fast. */
    l = "$L,42,100,ON*00";
    CHECK(sm_decode_cmd(l, strlen(l), &c, &code) == SM_ERR_CHECKSUM);
    CHECK(c.seq_valid && c.seq == 42 && code == SM_K_BAD_CHECKSUM);

    /* An uplink type sent down is an unknown command. */
    l = "$W,17,523040,A1,1840,1*31";
    CHECK(sm_decode_cmd(l, strlen(l), &c, &code) == SM_ERR_UNKNOWN_TYPE);
    CHECK(code == SM_K_UNKNOWN_CMD && c.seq == 17);

    /* Out-of-range servo angle is a bad argument. */
    l = "$V,43,800300,270*43";
    CHECK(sm_decode_cmd(l, strlen(l), &c, &code) == SM_ERR_FIELDS);
    CHECK(code == SM_K_BAD_ARG && c.seq == 43);

    /* Garbage: no seq to answer to. */
    l = "hello";
    CHECK(sm_decode_cmd(l, strlen(l), &c, &code) == SM_ERR_FRAMING);
    CHECK(!c.seq_valid);
}

static void test_uplink_decode_values(void)
{
    sm_up_t u;
    const char *l = "$E,27,672000,3,,,*70";
    CHECK(sm_decode_up(l, strlen(l), &u) == SM_OK);
    CHECK(u.nv == 4 && u.v[0] == 3 && u.v[1] == SM_NONE && u.v[3] == SM_NONE);

    l = "$M,21,530002,m1,S,TILT,90,40,2500,62*62";
    CHECK(sm_decode_up(l, strlen(l), &u) == SM_OK);
    CHECK(strcmp(u.id, "m1") == 0 && strcmp(u.word, "S") == 0 && strcmp(u.word2, "TILT") == 0);
    CHECK(u.nv == 4 && u.v[0] == 90 && u.v[3] == 62);

    l = "$Q,29,700100,2,-320,1450,0,410,2210,-12*74";
    CHECK(sm_decode_up(l, strlen(l), &u) == SM_OK);
    CHECK(u.nv == 7 && u.v[1] == -320 && u.v[6] == -12);

    l = "$H,31,720000,720,6144,38,0,0,POR*3B";
    CHECK(sm_decode_up(l, strlen(l), &u) == SM_OK);
    CHECK(u.ms == 720000u && u.v[1] == 6144 && strcmp(u.word, "POR") == 0);
}

static void test_frame_edge_cases(void)
{
    sm_frame_t fr;
    char line[160];     /* body (up to 128) + "$", "*XX" and CRLF: keeps gcc -Wformat-truncation quiet */

    /* Exactly 96 bytes with \r\n is fine, 97 is not: "$R,1,1,<id>*XX\r\n". */
    char body[128] = "R,1,1,";
    size_t blen = strlen(body);
    while (blen < SM_MAX_LINE - 6) {
        body[blen++] = 'a';
    }
    body[blen] = '\0';
    snprintf(line, sizeof line, "$%s*%02X\r\n", body, sm_xor(body, blen));
    CHECK(strlen(line) == SM_MAX_LINE);
    CHECK(sm_parse_frame(line, strlen(line), &fr) == SM_OK);
    body[blen++] = 'a';
    body[blen] = '\0';
    snprintf(line, sizeof line, "$%s*%02X\r\n", body, sm_xor(body, blen));
    CHECK(sm_parse_frame(line, strlen(line), &fr) == SM_ERR_TOO_LONG);

    /* \n alone is accepted as a terminator; seq/ms out of range is framing. */
    CHECK(sm_parse_frame("$R,1,1,a*1F\n", 12, &fr) == sm_parse_frame("$R,1,1,a*1F", 11, &fr));
    snprintf(body, sizeof body, "R,256,1,a");
    snprintf(line, sizeof line, "$%s*%02X", body, sm_xor(body, strlen(body)));
    CHECK(sm_parse_frame(line, strlen(line), &fr) == SM_ERR_FRAMING);
    snprintf(body, sizeof body, "R,1,-5,a");
    snprintf(line, sizeof line, "$%s*%02X", body, sm_xor(body, strlen(body)));
    CHECK(sm_parse_frame(line, strlen(line), &fr) == SM_ERR_FRAMING);
    snprintf(body, sizeof body, "R,1,4294967296,a");      /* ms > uint32 */
    snprintf(line, sizeof line, "$%s*%02X", body, sm_xor(body, strlen(body)));
    CHECK(sm_parse_frame(line, strlen(line), &fr) == SM_ERR_FRAMING);
    snprintf(body, sizeof body, "R,1,4294967295,a");      /* ms == uint32 max is fine */
    snprintf(line, sizeof line, "$%s*%02X", body, sm_xor(body, strlen(body)));
    CHECK(sm_parse_frame(line, strlen(line), &fr) == SM_OK && fr.ms == UINT32_MAX);

    /* Non-ASCII bytes are framing errors, not crashes. */
    CHECK(sm_parse_frame("$R,1,1,\xC3\xA9*00", 12, &fr) == SM_ERR_FRAMING);
    /* Two '$' or two '*' are framing errors. */
    CHECK(sm_parse_frame("$R,1,$1*00", 10, &fr) == SM_ERR_FRAMING);
    CHECK(sm_parse_frame("$R,1,1**00", 10, &fr) == SM_ERR_FRAMING);
    CHECK(sm_parse_frame("", 0, &fr) == SM_ERR_FRAMING);
}

static void test_line_assembler(void)
{
    sm_asm_t a;
    sm_asm_init(&a);
    const char *stream = "\x00\xffgarbage$W,17,523040,A1,1840,1*31\r\n$D,24,610140,do";
    size_t got = 0;
    char last[SM_MAX_LINE + 1] = "";
    for (size_t i = 0; i < 52; i++) {
        size_t n = sm_asm_push(&a, (uint8_t)stream[i]);
        if (n) {
            got++;
            memcpy(last, a.buf, n + 1);
        }
    }
    CHECK(got == 1);
    CHECK_STR(last, "$W,17,523040,A1,1840,1*31\r\n");

    /* A line that runs past 96 bytes without "\n" is dropped, then recovery. */
    sm_asm_init(&a);
    sm_asm_push(&a, '$');
    for (int i = 0; i < 200; i++) {
        CHECK(sm_asm_push(&a, 'x') == 0);
    }
    CHECK(a.dropped == 1);
    const char *ok = "$R,1,1,a*1F\n";
    size_t n = 0;
    for (size_t i = 0; i < strlen(ok); i++) {
        n = sm_asm_push(&a, (uint8_t)ok[i]);
    }
    CHECK(n == strlen(ok));
    CHECK(sm_parse_frame(a.buf, n, &(sm_frame_t){0}) == SM_OK);

    /* A '$' mid-line restarts (the torn line is counted). */
    sm_asm_init(&a);
    const char *torn = "$W,1,2$R,1,1,a*1F\n";
    n = 0;
    for (size_t i = 0; i < strlen(torn); i++) {
        n = sm_asm_push(&a, (uint8_t)torn[i]);
    }
    CHECK(n == strlen("$R,1,1,a*1F\n"));
    CHECK(a.dropped == 1);
}

static void test_seq_tracker(void)
{
    sm_seq_t s;
    sm_seq_init(&s);
    CHECK(sm_seq_update(&s, 250) == 0);
    CHECK(sm_seq_update(&s, 251) == 0);
    CHECK(sm_seq_update(&s, 254) == 2);
    CHECK(sm_seq_update(&s, 1) == 2);      /* wraps: 255, 0 lost */
    CHECK(s.lost == 4);
}

static void test_binary_building_blocks(void)
{
    for (size_t i = 0; i < COUNT(GOLDEN_BINARY); i++) {
        const golden_binary_t *g = &GOLDEN_BINARY[i];
        uint8_t enc[300];
        uint8_t dec[300];
        CHECK(sm_crc16(g->data, g->len) == g->crc);
        size_t n = sm_cobs_encode(g->data, g->len, enc, sizeof enc);
        CHECK(n == g->cobs_len);
        CHECK(memcmp(enc, g->cobs, n) == 0);
        for (size_t k = 0; k < n; k++) {
            CHECK(enc[k] != 0u);
        }
        size_t m = sm_cobs_decode(enc, n, dec, sizeof dec);
        CHECK(m == g->len);
        CHECK(g->len == 0 || memcmp(dec, g->data, m) == 0);
    }
    const uint8_t check[] = "123456789";
    CHECK(sm_crc16(check, 9) == 0x29B1u);      /* the published check value */

    uint8_t small[2];
    CHECK(sm_cobs_encode((const uint8_t *)"abc", 3, small, sizeof small) == 0);
    const uint8_t corrupt[] = {0x03, 0x11, 0x00};
    CHECK(sm_cobs_decode(corrupt, sizeof corrupt, small, sizeof small) == 0);
}

int main(void)
{
    test_golden_valid();
    test_golden_invalid();
    test_typed_encoders_match_doc();
    test_encoders_refuse_bad_input();
    test_command_decode();
    test_uplink_decode_values();
    test_frame_edge_cases();
    test_line_assembler();
    test_seq_tracker();
    test_binary_building_blocks();
    printf("%d checks, %d failures (%zu valid + %zu invalid doc examples, %zu binary vectors)\n",
           g_checks, g_failures, COUNT(GOLDEN_VALID), COUNT(GOLDEN_INVALID), COUNT(GOLDEN_BINARY));
    return g_failures == 0 ? 0 : 1;
}
