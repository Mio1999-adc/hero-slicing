#include "../control/private-rc-extension.h"
#include "xApp/e42_xapp_api.h"
#include "xApp/e2_node_arr_xapp.h"
#include "sm/rc_sm/rc_sm_id.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int find_function(const e2_node_connected_xapp_t *node,uint16_t id) {
  for(size_t i=0;i<node->len_rf;i++) if(node->rf[i].id==id) return (int)i;
  return -1;
}

static int build_control(rc_ctrl_req_data_t *request,uint32_t styleId,int bwp,int maxMcs) {
  memset(request,0,sizeof(*request));
  request->hdr.format=FORMAT_2_E2SM_RC_CTRL_HDR;
  request->msg.format=FORMAT_2_E2SM_RC_CTRL_MSG;
  request->msg.frmt_2.sz_seq_ctrl_sma=1;
  request->msg.frmt_2.action=calloc(1,sizeof(seq_ctrl_sma_t));
  if(!request->msg.frmt_2.action) return 0;
  seq_ctrl_sma_t *style=request->msg.frmt_2.action;
  style->ctrl_style=styleId;
  style->sz_seq_ctrl_act=1;
  style->seq_ctrl_act=calloc(1,sizeof(seq_ctrl_act_t));
  if(!style->seq_ctrl_act) return 0;
  seq_ctrl_act_t *action=style->seq_ctrl_act;
  action->ctrl_act_id=HERO_PRIVATE_RC_ACTION_SET_MAX_DL_MCS;
  action->ctrl_msg_frmt_1=calloc(1,sizeof(e2sm_rc_ctrl_msg_frmt_1_t));
  if(!action->ctrl_msg_frmt_1) return 0;
  action->ctrl_msg_frmt_1->sz_ran_param=2;
  action->ctrl_msg_frmt_1->ran_param=calloc(2,sizeof(seq_ran_param_t));
  if(!action->ctrl_msg_frmt_1->ran_param) return 0;
  const uint32_t ids[2]={HERO_PRIVATE_RC_PARAM_BWP_INDEX,HERO_PRIVATE_RC_PARAM_MAX_DL_MCS};
  const int64_t values[2]={bwp,maxMcs};
  for(size_t i=0;i<2;i++) {
    seq_ran_param_t *parameter=&action->ctrl_msg_frmt_1->ran_param[i];
    parameter->ran_param_id=ids[i];
    parameter->ran_param_val.type=ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
    parameter->ran_param_val.flag_true=calloc(1,sizeof(ran_parameter_value_t));
    if(!parameter->ran_param_val.flag_true) return 0;
    parameter->ran_param_val.flag_true->type=INTEGER_RAN_PARAMETER_VALUE;
    parameter->ran_param_val.flag_true->int_ran=values[i];
  }
  return 1;
}

int main(int argc,char **argv) {
  uint32_t targetGnb=102,maxMcs=1;
  const char *targetEnv=getenv("HERO_TARGET_GNB"),*mcsEnv=getenv("HERO_MAX_DL_MCS");
  if(targetEnv) targetGnb=(uint32_t)strtoul(targetEnv,NULL,10);
  if(mcsEnv) maxMcs=(uint32_t)strtoul(mcsEnv,NULL,10);
  fr_args_t args=init_fr_args(argc,argv);
  if(maxMcs>30) { fprintf(stderr,"MaxDlMcs must be in 0..30\n"); return 2; }
  init_xapp_api(&args);
  sleep(1);
  e2_node_arr_xapp_t nodes=e2_nodes_xapp_api();
  if(nodes.len<1) { fprintf(stderr,"Expected at least one registered native E2 node, got %d\n",nodes.len); free_e2_node_arr_xapp(&nodes); return 3; }
  uint32_t ids[16]; size_t idCount=0;
  e2_node_connected_xapp_t *target=NULL;
  uint32_t styleId=0;
  for(size_t i=0;i<nodes.len;i++) {
    e2_node_connected_xapp_t *node=&nodes.n[i];
    uint32_t gnbId=node->id.nb_id.nb_id;
    if(idCount<16) ids[idCount++]=gnbId;
    int index=find_function(node,SM_RC_ID);
    if(index<0 || node->rf[index].defn.type!=RC_RAN_FUNC_DEF_E || !node->rf[index].defn.rc.ctrl) continue;
    e2sm_rc_func_def_t *definition=&node->rf[index].defn.rc;
    for(size_t j=0;j<definition->ctrl->sz_seq_ctrl_style;j++) {
      seq_ctrl_style_t *style=&definition->ctrl->seq_ctrl_style[j];
      if(style->style_type==HERO_PRIVATE_RC_STYLE_ID && style->sz_seq_ctrl_act==1 &&
         style->seq_ctrl_act[0].id==HERO_PRIVATE_RC_ACTION_SET_MAX_DL_MCS) styleId=style->style_type;
    }
    if(gnbId==targetGnb) target=node;
  }
  if(!target || !styleId) { fprintf(stderr,"Target gNB or advertised private RC action unavailable\n"); free_e2_node_arr_xapp(&nodes); return 4; }
  printf("[HERO CONTROL XAPP] registered_nodes=%d identities=",nodes.len);
  for(size_t i=0;i<idCount;i++) printf("%s%u",i?",":"",ids[i]);
  printf(" target_gnb=%u private_style=%u requested_MaxDlMcs=%u\n",targetGnb,styleId,maxMcs);
  rc_ctrl_req_data_t request={0};
  if(!build_control(&request,styleId,0,(int)maxMcs)) { free_rc_ctrl_req_data(&request); free_e2_node_arr_xapp(&nodes); return 5; }
  sm_ans_xapp_t answer=control_sm_xapp_api(&target->id,SM_RC_ID,&request);
  free_rc_ctrl_req_data(&request);
  if(!answer.success) { fprintf(stderr,"FlexRIC xApp control request failed: %s\n",answer.u.reason?answer.u.reason:"unknown"); free_e2_node_arr_xapp(&nodes); return 6; }
  printf("[HERO CONTROL XAPP] CONTROL_RESPONSE target_gnb=%u requested_MaxDlMcs=%u\n",targetGnb,maxMcs);
  free_e2_node_arr_xapp(&nodes);
  while(try_stop_xapp_api()==false) usleep(1000);
  return 0;
}
