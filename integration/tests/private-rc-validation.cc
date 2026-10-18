#include "control/private-rc-extension.h"
#include "e2-agent/private-rc-validation.h"
#include "util/byte_array.h"
#include <array>
#include <cassert>
#include <cstdlib>
#include <utility>

// FlexRIC exposes these C99 VLA declarations in rc_dec_asn.h; declare the
// same C ABI with pointer parameters for standard C++ compilation.
extern "C" {
#include "sm/rc_sm/enc/rc_enc_asn.h"
e2sm_rc_ctrl_hdr_t rc_dec_ctrl_hdr_asn(size_t, const uint8_t*);
e2sm_rc_ctrl_msg_t rc_dec_ctrl_msg_asn(size_t, const uint8_t*);
}

using namespace hero::oran;

static rc_ctrl_req_data_t makeRequest(int bwp,int maxDlMcs) {
  rc_ctrl_req_data_t r{};
  r.hdr.format=FORMAT_2_E2SM_RC_CTRL_HDR;
  r.msg.format=FORMAT_2_E2SM_RC_CTRL_MSG;
  r.msg.frmt_2.sz_seq_ctrl_sma=1;
  r.msg.frmt_2.action=static_cast<seq_ctrl_sma_t*>(std::calloc(1,sizeof(seq_ctrl_sma_t)));
  assert(r.msg.frmt_2.action);
  auto& style=r.msg.frmt_2.action[0]; style.ctrl_style=HERO_PRIVATE_RC_STYLE_ID;
  style.sz_seq_ctrl_act=1;
  style.seq_ctrl_act=static_cast<seq_ctrl_act_t*>(std::calloc(1,sizeof(seq_ctrl_act_t)));
  assert(style.seq_ctrl_act);
  auto& action=style.seq_ctrl_act[0]; action.ctrl_act_id=HERO_PRIVATE_RC_ACTION_SET_MAX_DL_MCS;
  action.ctrl_msg_frmt_1=static_cast<e2sm_rc_ctrl_msg_frmt_1_t*>(std::calloc(1,sizeof(e2sm_rc_ctrl_msg_frmt_1_t)));
  assert(action.ctrl_msg_frmt_1);
  action.ctrl_msg_frmt_1->sz_ran_param=2;
  action.ctrl_msg_frmt_1->ran_param=static_cast<seq_ran_param_t*>(std::calloc(2,sizeof(seq_ran_param_t)));
  assert(action.ctrl_msg_frmt_1->ran_param);
  const uint32_t ids[]={HERO_PRIVATE_RC_PARAM_BWP_INDEX,HERO_PRIVATE_RC_PARAM_MAX_DL_MCS};
  const int64_t values[]={bwp,maxDlMcs};
  for(size_t i=0;i<2;i++) {
    auto& p=action.ctrl_msg_frmt_1->ran_param[i]; p.ran_param_id=ids[i];
    p.ran_param_val.type=ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
    p.ran_param_val.flag_true=static_cast<ran_parameter_value_t*>(std::calloc(1,sizeof(ran_parameter_value_t)));
    assert(p.ran_param_val.flag_true);
    p.ran_param_val.flag_true->type=INTEGER_RAN_PARAMETER_VALUE;
    p.ran_param_val.flag_true->int_ran=values[i];
  }
  return r;
}

int main() {
  auto request=makeRequest(0,17);
  std::int64_t bwp=-1,mcs=-1;
  assert(detail::ValidatePrivateRcControlRequest(&request,bwp,mcs) && bwp==0 && mcs==17);

  auto encodedHeader=rc_enc_ctrl_hdr_asn(&request.hdr);
  auto encodedMessage=rc_enc_ctrl_msg_asn(&request.msg);
  assert(encodedHeader.buf && encodedHeader.len>0 && encodedMessage.buf && encodedMessage.len>0);
  auto decodedHeader=rc_dec_ctrl_hdr_asn(encodedHeader.len,encodedHeader.buf);
  auto decodedMessage=rc_dec_ctrl_msg_asn(encodedMessage.len,encodedMessage.buf);
  rc_ctrl_req_data_t decoded{.hdr=decodedHeader,.msg=decodedMessage};
  assert(detail::ValidatePrivateRcControlRequest(&decoded,bwp,mcs) && bwp==0 && mcs==17);
  free_rc_ctrl_req_data(&decoded);
  free_byte_array(encodedHeader); free_byte_array(encodedMessage);
  free_rc_ctrl_req_data(&request);

  for(const auto [badBwp,badMcs]:std::array{std::pair{1,17},std::pair{0,-1},std::pair{0,31}}) {
    auto invalid=makeRequest(badBwp,badMcs);
    assert(!detail::ValidatePrivateRcControlRequest(&invalid,bwp,mcs));
    free_rc_ctrl_req_data(&invalid);
  }
  auto wrongStyle=makeRequest(0,17);
  wrongStyle.msg.frmt_2.action[0].ctrl_style++;
  assert(!detail::ValidatePrivateRcControlRequest(&wrongStyle,bwp,mcs));
  free_rc_ctrl_req_data(&wrongStyle);
  auto wrongType=makeRequest(0,17);
  wrongType.msg.frmt_2.action[0].seq_ctrl_act[0].ctrl_msg_frmt_1->ran_param[1].ran_param_val.flag_true->type=REAL_RAN_PARAMETER_VALUE;
  assert(!detail::ValidatePrivateRcControlRequest(&wrongType,bwp,mcs));
  free_rc_ctrl_req_data(&wrongType);
  auto duplicate=makeRequest(0,17);
  duplicate.msg.frmt_2.action[0].seq_ctrl_act[0].ctrl_msg_frmt_1->ran_param[1].ran_param_id=HERO_PRIVATE_RC_PARAM_BWP_INDEX;
  assert(!detail::ValidatePrivateRcControlRequest(&duplicate,bwp,mcs));
  free_rc_ctrl_req_data(&duplicate);
}
