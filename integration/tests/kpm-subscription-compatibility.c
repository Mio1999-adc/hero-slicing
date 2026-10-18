#include "e2-agent/flexric-agent-internal.h"
#include "sm/kpm_sm/kpm_sm_v03.00/enc/kpm_enc_asn.h"
#include "sm/kpm_sm/kpm_sm_v03.00/ie/kpm_data_ie.h"
#include "util/byte_array.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
  ric_subscription_request_t request;
  kpm_act_def_t action;
} fixture_t;

static fixture_t make_request(const char *name, uint32_t event_ms, uint32_t action_ms) {
  fixture_t fixture = {0};
  kpm_event_trigger_def_t event = {.type=FORMAT_1_RIC_EVENT_TRIGGER};
  event.kpm_ric_event_trigger_format_1.report_period_ms=event_ms;
  fixture.request.event_trigger=kpm_enc_event_trigger_asn(&event);
  fixture.request.ric_id.ran_func_id=2;
  fixture.request.len_action=1;
  fixture.request.action=calloc(1,sizeof(*fixture.request.action));
  fixture.request.action[0].type=RIC_ACT_REPORT;
  fixture.request.action[0].definition=calloc(1,sizeof(byte_array_t));

  fixture.action.type=FORMAT_1_ACTION_DEFINITION;
  fixture.action.frm_1.gran_period_ms=action_ms;
  fixture.action.frm_1.meas_info_lst_len=1;
  fixture.action.frm_1.meas_info_lst=calloc(1,sizeof(meas_info_format_1_lst_t));
  meas_info_format_1_lst_t *measurement=fixture.action.frm_1.meas_info_lst;
  measurement->meas_type.type=NAME_MEAS_TYPE;
  measurement->meas_type.name=cp_str_to_ba(name);
  measurement->label_info_lst_len=1;
  measurement->label_info_lst=calloc(1,sizeof(label_info_lst_t));
  measurement->label_info_lst[0].noLabel=malloc(sizeof(enum_value_e));
  *measurement->label_info_lst[0].noLabel=TRUE_ENUM_VALUE;
  *fixture.request.action[0].definition=kpm_enc_action_def_asn(&fixture.action);
  return fixture;
}

static void free_request(fixture_t *fixture) {
  free_byte_array(fixture->request.event_trigger);
  free_byte_array(*fixture->request.action[0].definition);
  free(fixture->request.action[0].definition);
  free(fixture->request.action);
  free_kpm_action_def(&fixture->action);
}

int main(void) {
  uint32_t period=0;
  fixture_t supported=make_request("HERO.DL.PDCP.SDU.Throughput.Mbps",1000,1000);
  assert(hero_kpm_subscription_supported(&supported.request,&period));
  assert(period==1000);
  free_request(&supported);

  fixture_t unknown=make_request("Invented.Measurement",1000,1000);
  assert(!hero_kpm_subscription_supported(&unknown.request,&period));
  free_request(&unknown);

  fixture_t mismatched=make_request("HERO.UL.PDCP.SDU.Throughput.Mbps",1000,500);
  assert(!hero_kpm_subscription_supported(&mismatched.request,&period));
  free_request(&mismatched);
  return 0;
}
