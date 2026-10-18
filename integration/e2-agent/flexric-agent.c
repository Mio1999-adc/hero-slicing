#include "flexric-agent.h"
#include "agent/e2_agent.h"
#include "agent/msg_handler_agent.h"
#include "agent/not_handler_agent.h"
#include "agent/gen_msg_agent.h"
#include "lib/ep/e2ap_ep.h"
#include "sm/kpm_sm/kpm_sm_v03.00/kpm_sm_agent.h"
#include "sm/kpm_sm/kpm_sm_v03.00/kpm_sm_id.h"
#include "sm/kpm_sm/kpm_sm_v03.00/dec/kpm_dec_asn.h"
#include "sm/kpm_sm/kpm_sm_v03.00/ie/kpm_data_ie.h"
#include "sm/rc_sm/rc_sm_agent.h"
#include "sm/rc_sm/rc_sm_id.h"
#include "sm/rc_sm/ie/rc_data_ie.h"
#include "control/private-rc-extension.h"
#include "sm/agent_if/read/sm_ag_if_rd.h"
#include "agent/msg_handler_agent.h"
#include "util/byte_array.h"
#include "util/compare.h"
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <time.h>
#include <sys/stat.h>
#include <stdatomic.h>

struct hero_e2_agent {
  hero_e2_config config;
  char *ip, *plugins;
  void *kpm_plugin_lifetime_hold, *rc_plugin_lifetime_hold;
  e2_agent_t *agent;
  pthread_t thread;
  pthread_mutex_t mutex;
  pthread_cond_t condition;
  atomic_bool stop_requested;
  hero_e2_status status;
  bool pending_kpm_size;
};
static _Thread_local hero_e2_agent *current;

static void update(hero_e2_agent *h, hero_e2_state state, const char *detail) {
  pthread_mutex_lock(&h->mutex);
  h->status.state = state;
  snprintf(h->status.detail, sizeof(h->status.detail), "%s", detail);
  pthread_cond_broadcast(&h->condition);
  pthread_mutex_unlock(&h->mutex);
  printf("[HERO E2] state=%d %s\n", state, detail);
}
static global_e2_node_id_t identity(const hero_e2_config *c) {
  return (global_e2_node_id_t){.type=ngran_gNB,
    .plmn={.mcc=c->mcc,.mnc=c->mnc,.mnc_digit_len=c->mnc_digits},
    .nb_id={.nb_id=c->gnb_id,.unused=0}}; // installed 32-bit ID encoding
}
static int valid(const hero_e2_config *c) {
  struct in_addr address;
  return c && c->ric_ip && inet_pton(AF_INET,c->ric_ip,&address)==1 &&
    c->port>0 && c->port<65535 && c->mcc>0 && c->mcc<=999 &&
    (c->mnc_digits==2 || c->mnc_digits==3) &&
    c->mnc < (c->mnc_digits==2 ? 100 : 1000) && c->gnb_id>0 &&
    c->connect_timeout_ms>0;
}
static void kpm_setup(void *data) {
  kpm_e2_setup_t *setup = data;
  static const char *const names[]={"HERO.DL.PDCP.SDU.Throughput.Mbps","HERO.UL.PDCP.SDU.Throughput.Mbps"};
  kpm_ran_function_def_t *def=&setup->ran_func_def;
  def->sz_ric_event_trigger_style_list=1;
  def->ric_event_trigger_style_list=calloc(1,sizeof(*def->ric_event_trigger_style_list));
  def->ric_event_trigger_style_list[0]=(ric_event_trigger_style_item_t){
    .style_type=STYLE_1_RIC_EVENT_TRIGGER,.style_name=cp_str_to_ba("Periodic"),
    .format_type=FORMAT_1_RIC_EVENT_TRIGGER};
  def->sz_ric_report_style_list=1;
  def->ric_report_style_list=calloc(1,sizeof(*def->ric_report_style_list));
  ric_report_style_item_t *style=def->ric_report_style_list;
  style->report_style_type=STYLE_1_RIC_SERVICE_REPORT;
  style->report_style_name=cp_str_to_ba("HERO cell measurements");
  style->act_def_format_type=FORMAT_1_ACTION_DEFINITION;
  style->ind_hdr_format_type=FORMAT_1_INDICATION_HEADER;
  style->ind_msg_format_type=FORMAT_1_INDICATION_MESSAGE;
  style->meas_info_for_action_lst_len=sizeof(names)/sizeof(names[0]);
  style->meas_info_for_action_lst=calloc(style->meas_info_for_action_lst_len,sizeof(*style->meas_info_for_action_lst));
  for(size_t i=0;i<style->meas_info_for_action_lst_len;i++)
    style->meas_info_for_action_lst[i].name=cp_str_to_ba(names[i]);
}
static void rc_setup(void *data) {
  rc_e2_setup_t *setup=data;
  e2sm_rc_func_def_t *def=&setup->ran_func_def;
  def->name.name=cp_str_to_ba("HERO-LENA-Private-RC");
  def->name.oid=cp_str_to_ba(SM_RAN_CTRL_OID);
  def->name.description=cp_str_to_ba("Private HERO integration extension: per-cell scheduler MaxDlMcs control");
  def->ctrl=calloc(1,sizeof(*def->ctrl));
  def->ctrl->sz_seq_ctrl_style=1;
  def->ctrl->seq_ctrl_style=calloc(1,sizeof(*def->ctrl->seq_ctrl_style));
  seq_ctrl_style_t *style=def->ctrl->seq_ctrl_style;
  style->style_type=HERO_PRIVATE_RC_STYLE_ID;
  style->name=cp_str_to_ba("HERO private cell scheduler control");
  style->hdr=FORMAT_2_E2SM_RC_CTRL_HDR;
  style->msg=FORMAT_2_E2SM_RC_CTRL_MSG;
  style->out_frmt=FORMAT_3_E2SM_RC_CTRL_OUT;
  style->sz_seq_ctrl_act=1;
  style->seq_ctrl_act=calloc(1,sizeof(*style->seq_ctrl_act));
  seq_ctrl_act_2_t *action=style->seq_ctrl_act;
  action->id=HERO_PRIVATE_RC_ACTION_SET_MAX_DL_MCS;
  action->name=cp_str_to_ba("Set cell/BWP MaxDlMcs");
  action->sz_seq_assoc_ran_param=2;
  action->assoc_ran_param=calloc(2,sizeof(*action->assoc_ran_param));
  action->assoc_ran_param[0].id=HERO_PRIVATE_RC_PARAM_BWP_INDEX;
  action->assoc_ran_param[0].name=cp_str_to_ba("BWP index");
  action->assoc_ran_param[1].id=HERO_PRIVATE_RC_PARAM_MAX_DL_MCS;
  action->assoc_ran_param[1].name=cp_str_to_ba("Maximum DL MCS (0..30)");
  style->sz_ran_param_ctrl_out=2;
  style->ran_param_ctrl_out=calloc(2,sizeof(*style->ran_param_ctrl_out));
  style->ran_param_ctrl_out[0].id=HERO_PRIVATE_RC_OUTCOME_APPLIED;
  style->ran_param_ctrl_out[0].name=cp_str_to_ba("Applied");
  style->ran_param_ctrl_out[1].id=HERO_PRIVATE_RC_OUTCOME_EFFECTIVE_MAX_DL_MCS;
  style->ran_param_ctrl_out[1].name=cp_str_to_ba("Effective MaxDlMcs");
}
static sm_ag_if_ans_t rc_control(void const *data) {
  sm_ag_if_ans_t answer={.type=CTRL_OUTCOME_SM_AG_IF_ANS_V0};
  answer.ctrl_out.type=RAN_CTRL_V1_3_AGENT_IF_CTRL_ANS_V0;
  if(current && current->config.rc_control)
    current->config.rc_control(current->config.rc_context,data,&answer.ctrl_out.rc);
  return answer;
}
static bool read_kpm_indication(void *data) {
  if(!current || !current->config.read_kpm_indication) return false;
  bool available=current->config.read_kpm_indication(current->config.kpm_context,data)!=0;
  if(available) current->pending_kpm_size=true;
  return available;
}
// Observe the exact APER E2AP user message size after original FlexRIC encodes it.
// This link-time wrapper does not alter bytes, transport, or codec behavior.
void __real_e2ap_send_sctp_msg(const e2ap_ep_t *,sctp_msg_t *);
void __wrap_e2ap_send_sctp_msg(const e2ap_ep_t *ep,sctp_msg_t *msg) {
  __real_e2ap_send_sctp_msg(ep,msg);
  if(current && current->pending_kpm_size && msg && msg->type==SCTP_MSG_PAYLOAD) {
    printf("[HERO E2] KPM_SCTP_USER_MESSAGE gnb_id=%u e2ap_aper_bytes=%zu\n",
      current->config.gnb_id,msg->ba.len);
    current->pending_kpm_size=false;
  }
}
static sm_io_ag_ran_t callbacks(void) {
  sm_io_ag_ran_t io={0};
  io.read_setup_tbl[KPM_V3_0_AGENT_IF_E2_SETUP_ANS_V0]=kpm_setup;
  io.read_ind_tbl[KPM_STATS_V3_0]=read_kpm_indication;
  io.read_setup_tbl[RAN_CTRL_V1_3_AGENT_IF_E2_SETUP_ANS_V0]=rc_setup;
  io.write_ctrl_tbl[RAN_CONTROL_CTRL_V1_03]=rc_control;
  return io;
}

int hero_kpm_subscription_supported(const ric_subscription_request_t *sr, uint32_t *period_out) {
  if(!sr || sr->ric_id.ran_func_id!=SM_KPM_ID || sr->len_action!=1 || !sr->action ||
     sr->action[0].type!=RIC_ACT_REPORT || !sr->action[0].definition ||
     !sr->event_trigger.buf || !sr->event_trigger.len ||
     !sr->action[0].definition->buf || !sr->action[0].definition->len ||
     sr->event_trigger.len>4096 || sr->action[0].definition->len>65536) return 0;
  kpm_event_trigger_def_t event=kpm_dec_event_trigger_asn(sr->event_trigger.len,sr->event_trigger.buf);
  if(event.type!=FORMAT_1_RIC_EVENT_TRIGGER) { free_kpm_event_trigger_def(&event); return 0; }
  uint32_t period=event.kpm_ric_event_trigger_format_1.report_period_ms;
  free_kpm_event_trigger_def(&event);
  if(!period || period>10000) return 0;
  kpm_act_def_t action=kpm_dec_action_def_asn(sr->action[0].definition->len,sr->action[0].definition->buf);
  int supported=action.type==FORMAT_1_ACTION_DEFINITION;
  if(supported) {
    kpm_act_def_format_1_t *f=&action.frm_1;
    supported=f->gran_period_ms==period && f->meas_info_lst_len>0 && f->meas_info_lst_len<=2 &&
      f->meas_info_lst!=NULL && f->meas_bin_range_info_lst_len==0;
    int seen_dl=0,seen_ul=0;
    for(size_t i=0;supported && i<f->meas_info_lst_len;i++) {
      meas_info_format_1_lst_t *m=&f->meas_info_lst[i];
      if(m->meas_type.type!=NAME_MEAS_TYPE || m->label_info_lst_len!=1 ||
         !m->label_info_lst || !m->label_info_lst[0].noLabel ||
         *m->label_info_lst[0].noLabel!=TRUE_ENUM_VALUE ||
         !m->meas_type.name.buf || !m->meas_type.name.len) { supported=0; break; }
      if(cmp_str_ba("HERO.DL.PDCP.SDU.Throughput.Mbps",m->meas_type.name)==0) seen_dl++;
      else if(cmp_str_ba("HERO.UL.PDCP.SDU.Throughput.Mbps",m->meas_type.name)==0) seen_ul++;
      else supported=0;
    }
    supported=supported && seen_dl<=1 && seen_ul<=1 && (size_t)(seen_dl+seen_ul)==f->meas_info_lst_len;
  }
  free_kpm_action_def(&action);
  if(supported && period_out) *period_out=period;
  return supported;
}

static e2ap_msg_t subscription_failure(const ric_subscription_request_t *sr) {
  size_t count=sr->len_action ? sr->len_action : 1;
  ric_action_not_admitted_t *na=calloc(count,sizeof(*na));
  for(size_t i=0;i<count;i++) na[i]=(ric_action_not_admitted_t){
    .ric_act_id=sr->len_action && sr->action ? sr->action[i].id : 0,
    .cause={.present=CAUSE_RICREQUEST,.ricRequest=CAUSE_RIC_ACTION_NOT_SUPPORTED}};
  return (e2ap_msg_t){.type=RIC_SUBSCRIPTION_FAILURE,.u_msgs.ric_sub_fail={
    .ric_id=sr->ric_id,.not_admitted=na,.len_na=count}};
}

static e2ap_msg_t handle_kpm_subscription(e2_agent_t *ag,const e2ap_msg_t *msg) {
  const ric_subscription_request_t *sr=&msg->u_msgs.ric_sub_req;
  uint32_t period=0;
  if(!hero_kpm_subscription_supported(sr,&period)) {
    printf("[HERO KPM] RIC_SUBSCRIPTION_FAILURE request=%u function=%u reason=unsupported KPM v3 style, interval, label, or measurement\n",
      sr->ric_id.ric_req_id,sr->ric_id.ran_func_id);
    return subscription_failure(sr);
  }
  printf("[HERO KPM] subscription admitted request=%u action=%u granularity_ms=%u measurements=DL,UL\n",
    sr->ric_id.ric_req_id,sr->action[0].id,period);
  return e2ap_handle_subscription_request_agent(ag,msg);
}
static e2ap_msg_t response(e2_agent_t *ag, const e2ap_msg_t *msg) {
  e2ap_msg_t answer=e2ap_handle_setup_response_agent(ag,msg);
  const e2_setup_response_t *r=&msg->u_msgs.e2_stp_resp;
  bool acceptedKpm=false,acceptedRc=!current->config.rc_control;
  for (size_t i=0;i<r->len_acc;i++) {
    printf("[HERO E2] E2_SETUP_RESPONSE accepted RAN function ID=%u\n",r->accepted[i]);
    if(r->accepted[i]==SM_KPM_ID) acceptedKpm=true;
    if(r->accepted[i]==SM_RC_ID) acceptedRc=true;
  }
  size_t expected=current->config.rc_control?2:1;
  if(acceptedKpm && acceptedRc && r->len_acc==expected && r->len_rej==0) {
    pthread_mutex_lock(&current->mutex);
    current->status.accepted_function=SM_KPM_ID;
    if(current->config.rc_control) current->status.accepted_control=SM_RC_ID;
    pthread_mutex_unlock(&current->mutex);
    update(current,HERO_E2_REGISTERED,current->config.rc_control?
      "E2 Setup succeeded; KPM v3 and explicitly private HERO RC extension registered":
      "E2 Setup succeeded; KPM v3 registered (Periodic / HERO cell measurements)");
  } else {
    update(current,HERO_E2_SETUP_FAILED,"Setup Response did not accept the expected KPM/private-RC function set");
    ag->stop_token=true;
  }
  return answer;
}
static e2ap_msg_t failure(e2_agent_t *ag,const e2ap_msg_t *msg) {
  e2ap_msg_t answer=e2ap_handle_setup_failure_agent(ag,msg);
  const cause_t *cause=&msg->u_msgs.e2_stp_fail.cause;
  char detail[256];
  snprintf(detail,sizeof(detail),"E2 Setup Failure cause category=%d value=%d; retries disabled",cause->present,cause->ricRequest);
  update(current,HERO_E2_SETUP_FAILED,detail);
  ag->stop_token=true;
  return answer;
}
static e2ap_msg_t unsupported(e2_agent_t *ag,const e2ap_msg_t *msg) {
  char detail[256];
  snprintf(detail,sizeof(detail),"Procedure type=%d unavailable in this integration",msg->type);
  update(current,HERO_E2_UNSUPPORTED,detail);
  ag->stop_token=true;
  return (e2ap_msg_t){.type=NONE_E2_MSG_TYPE};
}
// Original loop has no notification observer. GNU linker interposition is scoped
// to targets linking this library; original wire transport/codec remain intact.
void __real_notification_handle_ag(e2_agent_t *,const sctp_msg_t *);
void __wrap_notification_handle_ag(e2_agent_t *ag,const sctp_msg_t *msg) {
  if(!current || current->agent!=ag) { __real_notification_handle_ag(ag,msg); return; }
  update(current,HERO_E2_TRANSPORT_FAILED,"SCTP shutdown/loss notification; automatic reconnect disabled");
  ag->stop_token=true;
}
static int64_t monotonic_ms(void) {
  struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
  return t.tv_sec*1000LL+t.tv_nsec/1000000;
}
static int associate(hero_e2_agent *h) {
  int fd=h->agent->ep.base.fd;
  int flags=fcntl(fd,F_GETFL,0);
  if(flags<0 || fcntl(fd,F_SETFL,flags|O_NONBLOCK)<0) return -1;
  int rc=connect(fd,(const struct sockaddr *)&h->agent->ep.to,sizeof(h->agent->ep.to));
  if(rc<0 && errno!=EINPROGRESS) return -1;
  int64_t end=monotonic_ms()+h->config.connect_timeout_ms;
  while(rc<0 && !h->stop_requested) {
    int remaining=(int)(end-monotonic_ms());
    if(remaining<=0) { errno=ETIMEDOUT; return -1; }
    struct pollfd p={.fd=fd,.events=POLLOUT};
    int n=poll(&p,1,remaining<50 ? remaining : 50);
    if(n<0 && errno==EINTR) continue;
    if(n<0) return -1;
    if(n==0) continue;
    int error=0; socklen_t len=sizeof(error);
    if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&len)<0) return -1;
    if(error) { errno=error; return -1; }
    rc=0;
  }
  if(h->stop_requested) { errno=ECANCELED; return -1; }
  if(fcntl(fd,F_SETFL,flags)<0) return -1;
  struct sctp_status status={0};
  struct sctp_paddrinfo peer={0};
  memcpy(&peer.spinfo_address,&h->agent->ep.to,sizeof(h->agent->ep.to));
  socklen_t peer_size=sizeof(peer);
  if(getsockopt(fd,IPPROTO_SCTP,SCTP_GET_PEER_ADDR_INFO,&peer,&peer_size)<0) return -1;
  status.sstat_assoc_id=peer.spinfo_assoc_id;
  socklen_t size=sizeof(status);
  if(getsockopt(fd,IPPROTO_SCTP,SCTP_STATUS,&status,&size)<0) return -1;
  if(status.sstat_state!=SCTP_ESTABLISHED) { errno=ENOTCONN; return -1; }
  printf("[HERO E2] SCTP_ESTABLISHED assoc_id=%d peer=%s:%u in_streams=%u out_streams=%u\n",
    status.sstat_assoc_id,h->ip,h->config.port,status.sstat_instrms,status.sstat_outstrms);
  return 0;
}
static void *run(void *arg) {
  hero_e2_agent *h=arg; current=h;
  // No simulated object access in this thread.
  char kpm_plugin[4096],rc_plugin[4096];
  int path_len=snprintf(kpm_plugin,sizeof(kpm_plugin),"%s/libkpm_sm.so",h->plugins);
  if(path_len<0 || (size_t)path_len>=sizeof(kpm_plugin) ||
     !(h->kpm_plugin_lifetime_hold=dlopen(kpm_plugin,RTLD_NOW|RTLD_LOCAL))) {
    update(h,HERO_E2_UNSUPPORTED,"Could not retain original KPM plugin for FlexRIC subscription cleanup");
    return NULL;
  }
  if(h->config.rc_control) {
    path_len=snprintf(rc_plugin,sizeof(rc_plugin),"%s/librc_sm.so",h->plugins);
    if(path_len<0 || (size_t)path_len>=sizeof(rc_plugin) ||
       !(h->rc_plugin_lifetime_hold=dlopen(rc_plugin,RTLD_NOW|RTLD_LOCAL))) {
      update(h,HERO_E2_UNSUPPORTED,"Could not retain original RC plugin for control cleanup");
      return NULL;
    }
  }
  e2_agent_t *ag=e2_init_agent(h->ip,h->config.port,identity(&h->config),callbacks(),h->plugins);
  e2ap_ep_init(&ag->ep.base); // original endpoint initializer omits mutex initialization
  pthread_mutex_lock(&h->mutex); h->agent=ag; pthread_mutex_unlock(&h->mutex);
  for(size_t i=0;i<ag->sz_handle_msg;i++) ag->handle_msg[i]=unsupported;
  ag->handle_msg[E2_SETUP_RESPONSE]=response;
  ag->handle_msg[E2_SETUP_FAILURE]=failure;
  ag->handle_msg[RIC_SUBSCRIPTION_REQUEST]=handle_kpm_subscription;
  ag->handle_msg[RIC_SUBSCRIPTION_DELETE_REQUEST]=e2ap_handle_subscription_delete_request_agent;
  if(h->config.rc_control) ag->handle_msg[RIC_CONTROL_REQUEST]=e2ap_handle_control_request_agent;
  printf("[HERO E2] identity PLMN=%03u%0*u gNB_ID=%u bits=32 RAN=ngran_gNB registry_count=%zu KPM_ID=%u revision=%u RC_private=%u\n",
    h->config.mcc,h->config.mnc_digits,h->config.mnc,h->config.gnb_id,size_plugin_ag(&ag->plugin),SM_KPM_ID,SM_KPM_REV,
    h->config.rc_control?SM_RC_ID:0);
  if(associate(h)<0) {
    char detail[256]; snprintf(detail,sizeof(detail),"SCTP association failed: %s",strerror(errno));
    update(h,HERO_E2_TRANSPORT_FAILED,detail);
    ag->agent_stopped=true;
    return NULL;
  }
  if(h->stop_requested) { ag->agent_stopped=true; return NULL; }
  update(h,HERO_E2_SETUP_PENDING,"SCTP established; transmitting E2 Setup Request via original FlexRIC");
  e2_start_agent(ag);
  return NULL;
}
static int plugin_directory_valid(const char *path,int withRc) {
  if(!path) return 0;
  DIR *dir=opendir(path); if(!dir) return 0;
  int kpm=0,rc=0,ok=1; struct dirent *entry;
  while((entry=readdir(dir))) {
    if(!strcmp(entry->d_name,".") || !strcmp(entry->d_name,"..")) continue;
    int isKpm=!strcmp(entry->d_name,"libkpm_sm.so");
    int isRc=!strcmp(entry->d_name,"librc_sm.so");
    if(!isKpm && (!withRc || !isRc)) { ok=0; break; }
    char file[4096]; struct stat st;
    snprintf(file,sizeof(file),"%s/%s",path,entry->d_name);
    if(stat(file,&st) || !S_ISREG(st.st_mode)) { ok=0; break; }
    if(isKpm) kpm++;
    if(isRc) rc++;
  }
  closedir(dir); return ok && kpm==1 && rc==(withRc?1:0);
}
hero_e2_agent *hero_e2_start(const hero_e2_config *c,char *error,unsigned size) {
  if(!valid(c) || !plugin_directory_valid(c->plugin_dir,c->rc_control!=NULL)) {
    snprintf(error,size,"Invalid IPv4 endpoint/PLMN/identity/timeout or service-model plugin directory"); return NULL;
  }
  hero_e2_agent *h=calloc(1,sizeof(*h));
  if(!h) { snprintf(error,size,"Agent allocation failed"); return NULL; }
  h->config=*c; h->ip=strdup(c->ric_ip);
  // Original loader concatenates filenames without adding a separator.
  h->plugins=malloc(strlen(c->plugin_dir)+2);
  if(!h->ip || !h->plugins) { free(h->ip); free(h->plugins); free(h); snprintf(error,size,"Allocation failed"); return NULL; }
  sprintf(h->plugins,"%s/",c->plugin_dir);
  h->config.ric_ip=h->ip; h->config.plugin_dir=h->plugins;
  pthread_mutex_init(&h->mutex,NULL); pthread_cond_init(&h->condition,NULL);
  h->status.state=HERO_E2_CONNECTING;
  int rc=pthread_create(&h->thread,NULL,run,h);
  if(rc) { snprintf(error,size,"pthread_create: %s",strerror(rc)); pthread_mutex_destroy(&h->mutex); pthread_cond_destroy(&h->condition); free(h->ip); free(h->plugins); free(h); return NULL; }
  return h;
}
hero_e2_status hero_e2_get_status(hero_e2_agent *h) {
  pthread_mutex_lock(&h->mutex); hero_e2_status s=h->status; pthread_mutex_unlock(&h->mutex); return s;
}
int hero_e2_wait(hero_e2_agent *h,uint32_t timeout,hero_e2_status *out) {
  struct timespec until; clock_gettime(CLOCK_REALTIME,&until);
  until.tv_sec+=timeout/1000; until.tv_nsec+=(timeout%1000)*1000000L;
  if(until.tv_nsec>=1000000000) { until.tv_sec++; until.tv_nsec-=1000000000; }
  pthread_mutex_lock(&h->mutex);
  while(h->status.state==HERO_E2_CONNECTING || h->status.state==HERO_E2_SETUP_PENDING) {
    if(pthread_cond_timedwait(&h->condition,&h->mutex,&until)==ETIMEDOUT) break;
  }
  *out=h->status;
  pthread_mutex_unlock(&h->mutex);
  return out->state==HERO_E2_REGISTERED;
}
void hero_e2_stop(hero_e2_agent *h) {
  if(!h) return;
  h->stop_requested=true;
  pthread_mutex_lock(&h->mutex);
  if(h->agent) h->agent->stop_token=true;
  pthread_mutex_unlock(&h->mutex);
  pthread_join(h->thread,NULL);
  e2_agent_t *ag=h->agent;
  if(ag) {
    // Original free releases maps/socket but leaves its epoll/pipe and pending fds.
    for(void *it=assoc_front(&ag->pending.left);it!=assoc_end(&ag->pending.left);it=assoc_next(&ag->pending.left,it)) {
      int fd=*(int *)assoc_key(&ag->pending.left,it);
      rm_fd_asio_agent(&ag->io,fd);
    }
    int epoll=ag->io.efd, read_pipe=ag->io.pipe.r, write_pipe=ag->io.pipe.w;
    e2_free_agent(ag);
    close(epoll); close(read_pipe); close(write_pipe);
  }
  // Original FlexRIC frees the plugin registry before its indication map, but
  // that map's cleanup callbacks live in the plugin. Keep the mapping loaded
  // across the original cleanup call, then release our extra dlopen reference.
  if(h->rc_plugin_lifetime_hold) dlclose(h->rc_plugin_lifetime_hold);
  if(h->kpm_plugin_lifetime_hold) dlclose(h->kpm_plugin_lifetime_hold);
  printf("[HERO E2] shutdown joined worker and released SCTP/epoll/pipe/timers\n");
  pthread_mutex_destroy(&h->mutex); pthread_cond_destroy(&h->condition);
  free(h->ip); free(h->plugins); free(h);
}
int hero_e2_identity_registration_check(const hero_e2_config *c) {
  if(!valid(c)) return 0;
  sm_agent_t *sm=make_kpm_sm_agent(callbacks());
  sm_e2_setup_data_t definition=sm->proc.on_e2_setup(sm);
  ran_function_t function={.id=sm->info.id(),.rev=sm->info.rev(),
    .defn={.buf=definition.ran_fun_def,.len=definition.len_rfd}};
  byte_array_t oid=cp_str_to_ba(sm->info.oid()); function.oid=&oid;
  e2ap_msg_t request={.type=E2_SETUP_REQUEST};
  request.u_msgs.e2_stp_req=(e2_setup_request_t){.id=identity(c),.len_rf=1,.ran_func_item=&function};
  e2ap_agent_t ap={0}; init_ap(&ap.base.type);
  byte_array_t bytes=e2ap_msg_enc_ag(&ap,&request);
  e2ap_msg_t decoded=e2ap_msg_dec_ag(&ap,bytes);
  e2_setup_request_t *d=&decoded.u_msgs.e2_stp_req;
  int ok=decoded.type==E2_SETUP_REQUEST && eq_global_e2_node_id(&d->id,&request.u_msgs.e2_stp_req.id) &&
    d->len_rf==1 && d->ran_func_item[0].id==2 && d->ran_func_item[0].rev==3;
  kpm_ran_function_def_t kpm=kpm_dec_func_def_asn(d->ran_func_item[0].defn.len,d->ran_func_item[0].defn.buf);
  ok=ok && kpm.name.name.len>0 && kpm.sz_ric_report_style_list==1 && kpm.sz_ric_event_trigger_style_list==1;
  if(ok) {
    ric_report_style_item_t *style=&kpm.ric_report_style_list[0];
    ok=style->report_style_type==STYLE_1_RIC_SERVICE_REPORT &&
      style->act_def_format_type==FORMAT_1_ACTION_DEFINITION &&
      style->ind_hdr_format_type==FORMAT_1_INDICATION_HEADER &&
      style->ind_msg_format_type==FORMAT_1_INDICATION_MESSAGE &&
      style->meas_info_for_action_lst_len==2;
  }
  free_kpm_ran_function_def(&kpm); e2ap_msg_free_ag(&ap,&decoded);
  free_byte_array(bytes); free_byte_array(oid); free(definition.ran_fun_def); sm->free_sm(sm);
  return ok;
}
