/*
 * app_config.h - compile-time switches and ids for one sensor node.
 *
 * Every sensor can be switched off twice (CLAUDE_CODE_PROMPT_V2 M5):
 *   - at compile time here (SM_HAVE_x 0 removes the driver from the image),
 *   - at run time with "$C,ENABLE,<sensor>,0|1" (stored in flash).
 * A sensor that is compiled out answers ENABLE with $K code 4.
 * The ids must match the Pi's config (sensors.cell_map, sensors.mems_nodes).
 */
#ifndef APP_CONFIG_H
#define APP_CONFIG_H

#ifndef SM_HAVE_HX711
#define SM_HAVE_HX711          1
#endif
#ifndef SM_HAVE_MEMS
#define SM_HAVE_MEMS           1
#endif
#ifndef SM_HAVE_IR_BEAM
#define SM_HAVE_IR_BEAM        1
#endif
#ifndef SM_HAVE_PIR
#define SM_HAVE_PIR            1
#endif
#ifndef SM_HAVE_BH1750
#define SM_HAVE_BH1750         1
#endif
#ifndef SM_HAVE_BME280
#define SM_HAVE_BME280         1
#endif
#ifndef SM_HAVE_BUZZER
#define SM_HAVE_BUZZER         1
#endif
#ifndef SM_HAVE_LED
#define SM_HAVE_LED            1
#endif
#ifndef SM_HAVE_SERVO
#define SM_HAVE_SERVO          1          /* compiled in, disabled at run time by default */
#endif
#ifndef SM_HAVE_RESTOCK
#define SM_HAVE_RESTOCK        1
#endif
#ifndef SM_HAVE_LD2450
#define SM_HAVE_LD2450         0          /* radar not purchased: not compiled */
#endif

/* Ids on the wire (docs/PROTOCOL.md: A-Z a-z 0-9 - _ .) */
#define SM_SLOT_IDS            {"1", "2"}          /* load-cell channels */
#define SM_MEMS_IDS            {"m1", "m2"}        /* m1 under shelf-a, m2 on the entrance camera */
#define SM_MEMS_ROLES          {'S', 'C'}
#define SM_BEAM_IDS            {"b1", "b2"}        /* outer, inner */
#define SM_DOOR_ID             "door1"
#define SM_PIR_ZONE            "aisle1"
#define SM_SHELF_ID            "shelf-a"

/* Timing (docs/PROTOCOL.md section 3) */
#define SM_HEALTH_PERIOD_MS    10000u
#define SM_ENV_PERIOD_MS       5000u
#define SM_LUX_PERIOD_MS       1000u
#define SM_WATCHDOG_MS         4000u
#define SM_BUZZER_AUTO_OFF_MS  5000u       /* a buzzer never screams forever */
#define SM_SEND_BEAM_EDGES     1           /* $B raw edges (debug); 0 saves UART bandwidth */

#endif /* APP_CONFIG_H */
