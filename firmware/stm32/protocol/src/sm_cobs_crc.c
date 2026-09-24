/*
 * sm_cobs_crc.c - framing pieces for the binary production mode
 * (docs/PROTOCOL.md section 6): CRC-16/CCITT-FALSE and COBS.
 *
 * COBS (Consistent Overhead Byte Stuffing) removes every 0x00 from a packet so
 * 0x00 can mark the end of a frame; overhead is at most one byte per 254.
 * Reference: https://www.embeddedrelated.com/showarticle/113.php
 */
#include "sm_proto.h"

uint16_t sm_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc & 0x8000u) ? (uint16_t)(((unsigned)crc << 1) ^ 0x1021u) : (uint16_t)((unsigned)crc << 1);
        }
    }
    return crc;
}

size_t sm_cobs_encode(const uint8_t *in, size_t len, uint8_t *out, size_t cap)
{
    size_t code_at = 0;     /* where the current block's length byte goes */
    size_t o = 1;
    uint8_t code = 1;
    if (cap == 0) {
        return 0;
    }
    for (size_t i = 0; i < len; i++) {
        if (in[i] == 0u) {
            out[code_at] = code;
            code_at = o++;
            code = 1;
        } else {
            if (o >= cap) {
                return 0;
            }
            out[o++] = in[i];
            code++;
            if (code == 0xFFu) {           /* full block of 254 data bytes */
                out[code_at] = code;
                code_at = o++;
                code = 1;
            }
        }
        if (o > cap) {
            return 0;
        }
    }
    if (code_at >= cap) {
        return 0;
    }
    out[code_at] = code;
    return o;
}

size_t sm_cobs_decode(const uint8_t *in, size_t len, uint8_t *out, size_t cap)
{
    size_t i = 0;
    size_t o = 0;
    while (i < len) {
        uint8_t code = in[i++];
        if (code == 0u) {
            return 0;                       /* a zero inside a frame is corruption */
        }
        for (uint8_t k = 1; k < code; k++) {
            if (i >= len || in[i] == 0u || o >= cap) {
                return 0;
            }
            out[o++] = in[i++];
        }
        if (code != 0xFFu && i < len) {
            if (o >= cap) {
                return 0;
            }
            out[o++] = 0u;                  /* the zero this block replaced */
        }
    }
    return o;
}
