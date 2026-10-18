// Compile installed original FlexRIC headers in C11, and force linker references.
// No pointed-to function is executed; this test never starts communication.
#include "agent/e2_agent_api.h"
#include "agent/e2_agent.h"
#include "agent/endpoint_agent.h"
#include "sm/kpm_sm/kpm_sm_v03.00/enc/kpm_enc_asn.h"
#include "sm/kpm_sm/kpm_sm_v03.00/dec/kpm_dec_asn.h"
#include "sm/rc_sm/enc/rc_enc_asn.h"
#include "sm/rc_sm/dec/rc_dec_asn.h"
int hero_flexric_link_probe(void) {
#define CHECK(symbol) do { __typeof__(&(symbol)) volatile fn = &(symbol); if (!fn) return 0; } while (0)
  CHECK(init_agent_api); CHECK(stop_agent_api); CHECK(async_event_agent_api);
  CHECK(e2_init_agent); CHECK(e2_start_agent); CHECK(e2_free_agent);
  CHECK(e2ap_init_ep_agent); CHECK(e2ap_recv_msg_agent); CHECK(e2ap_send_bytes_agent);
  CHECK(kpm_enc_ind_hdr_asn); CHECK(kpm_enc_ind_msg_asn);
  CHECK(kpm_dec_event_trigger_asn); CHECK(kpm_dec_action_def_asn);
  CHECK(rc_dec_ctrl_hdr_asn); CHECK(rc_dec_ctrl_msg_asn); CHECK(rc_enc_ctrl_out_asn);
#undef CHECK
  return 1;
}
