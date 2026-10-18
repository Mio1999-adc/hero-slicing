#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef enum { HERO_E2_CONNECTING, HERO_E2_SETUP_PENDING, HERO_E2_REGISTERED,
  HERO_E2_SETUP_FAILED, HERO_E2_TRANSPORT_FAILED, HERO_E2_UNSUPPORTED, HERO_E2_STOPPED } hero_e2_state;
typedef int (*hero_kpm_read_indication_fn)(void *context, void *flexric_request);
// The control request is a decoded original-FlexRIC RC v1.03 value; the
// callback returns an owned decoded RC outcome in the caller-provided storage.
typedef int (*hero_rc_control_fn)(void *context, const void *flexric_request,
                                  void *flexric_outcome);
typedef struct {
  const char *ric_ip, *plugin_dir;
  uint16_t port, mcc, mnc;
  uint8_t mnc_digits;
  uint32_t gnb_id, connect_timeout_ms;
  void *kpm_context;
  hero_kpm_read_indication_fn read_kpm_indication;
  void *rc_context;
  hero_rc_control_fn rc_control;
} hero_e2_config;
typedef struct hero_e2_agent hero_e2_agent;
typedef struct { hero_e2_state state; uint16_t accepted_function, accepted_control; char detail[256]; } hero_e2_status;
// Returns an owned worker handle. No ns-3 objects enter this C11 boundary.
hero_e2_agent *hero_e2_start(const hero_e2_config *, char *error, unsigned error_size);
int hero_e2_wait(hero_e2_agent *, uint32_t timeout_ms, hero_e2_status *);
hero_e2_status hero_e2_get_status(hero_e2_agent *);
void hero_e2_stop(hero_e2_agent *); // quiesce, join, release; exactly once per handle
int hero_e2_identity_registration_check(const hero_e2_config *);
#ifdef __cplusplus
}
#endif
