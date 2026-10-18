// Negative/rejection fixtures use original FlexRIC codecs and SCTP I/O.
// This server is a test fixture, never the production RIC validation.
#include "e2-agent/flexric-agent.h"
#include "agent/e2ap_agent.h"
#include "lib/ep/e2ap_ep.h"
#include "lib/e2ap/v1_01/enc/e2ap_msg_enc_asn.h"
#include "lib/e2ap/v1_01/ie/asn/E2AP-PDU.h"
#include "lib/e2ap/v1_01/ie/asn/UnsuccessfulOutcome.h"
#include "lib/e2ap/v1_01/ie/asn/ProtocolIE-Field.h"
#include "lib/e2ap/v1_01/ie/asn/ProtocolIE-ID.h"
#include <dirent.h>
#include <poll.h>
#include <pthread.h>
#include <stdio.h>
#include <time.h>

typedef struct {
  int fd, mode, valid_request;
  pthread_t thread;
  pthread_mutex_t mutex;
  pthread_cond_t condition;
  int release;
} fixture;
static int descriptor_count(void) {
  DIR *dir=opendir("/proc/self/fd"); if(!dir) return -1;
  int count=0; struct dirent *entry;
  while((entry=readdir(dir))) if(strcmp(entry->d_name,".") && strcmp(entry->d_name,"..")) count++;
  closedir(dir); return count;
}
static void *serve(void *arg) {
  fixture *f=arg;
  struct pollfd p={.fd=f->fd,.events=POLLIN};
  if(poll(&p,1,5000)>0) {
    e2ap_ep_t ep={.fd=f->fd}; e2ap_ep_init(&ep);
    sctp_msg_t input=e2ap_recv_sctp_msg(&ep);
    e2ap_agent_t ap={0}; init_ap(&ap.base.type);
    e2ap_msg_t request=e2ap_msg_dec_ag(&ap,input.ba);
    f->valid_request=request.type==E2_SETUP_REQUEST && request.u_msgs.e2_stp_req.len_rf==1 && request.u_msgs.e2_stp_req.ran_func_item[0].id==2;
    if(f->mode!=2) {
      accepted_ran_function_t id=f->mode==5 ? 99 : 2;
      e2ap_msg_t reply={.type=f->mode==1 ? E2_SETUP_FAILURE : E2_SETUP_RESPONSE};
      if(f->mode==1) {
        reply.u_msgs.e2_stp_fail.cause=(cause_t){.present=CAUSE_MISC,.misc=CAUSE_MISC_HARDWARE_FAILURE};
      } else {
        reply.u_msgs.e2_stp_resp.id.plmn=request.u_msgs.e2_stp_req.id.plmn;
        reply.u_msgs.e2_stp_resp.id.near_ric_id.double_word=25;
        reply.u_msgs.e2_stp_resp.accepted=&id;
        reply.u_msgs.e2_stp_resp.len_acc=1;
      }
      byte_array_t bytes;
      if(f->mode==1) {
        // Original V1 builder assigns IE 40 instead of installed Cause IE 1.
        // Normalize test fixture input only; all ASN.1 work uses original library.
        E2AP_PDU_t *pdu=e2ap_enc_setup_failure_asn_pdu(&reply.u_msgs.e2_stp_fail);
        pdu->choice.unsuccessfulOutcome->value.choice.E2setupFailure.protocolIEs.list.array[0]->id=ProtocolIE_ID_id_Cause;
        bytes=e2ap_enc_asn_pdu_ba(pdu);
        ASN_STRUCT_FREE(asn_DEF_E2AP_PDU_e2ap_v1_01,pdu);
      } else bytes=e2ap_msg_enc_ag(&ap,&reply);
      sctp_msg_t output={.info=input.info,.ba=bytes};
      e2ap_send_sctp_msg(&ep,&output); free_byte_array(bytes);
    }
    e2ap_msg_free_ag(&ap,&request); free_sctp_msg(&input);
    pthread_mutex_destroy(&ep.mtx); // fixture owns fd until joined
  }
  pthread_mutex_lock(&f->mutex);
  while(!f->release) pthread_cond_wait(&f->condition,&f->mutex);
  pthread_mutex_unlock(&f->mutex);
  return NULL;
}
static int test(int mode, const char *plugins) {
  int before=descriptor_count();
  fixture f={.mode=mode};
  pthread_mutex_init(&f.mutex,NULL); pthread_cond_init(&f.condition,NULL);
  f.fd=socket(AF_INET,SOCK_SEQPACKET,IPPROTO_SCTP);
  if(f.fd<0) { perror("SCTP fixture socket"); return 0; }
  struct sockaddr_in address={.sin_family=AF_INET,.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
  if(bind(f.fd,(struct sockaddr *)&address,sizeof(address))<0) return 0;
  socklen_t size=sizeof(address); getsockname(f.fd,(struct sockaddr *)&address,&size);
  if(mode!=3) { if(listen(f.fd,4)<0) return 0; pthread_create(&f.thread,NULL,serve,&f); }
  hero_e2_config config={.ric_ip="127.0.0.1",.plugin_dir=plugins,.port=ntohs(address.sin_port),
    .mcc=310,.mnc=260,.mnc_digits=3,.gnb_id=100+mode,.connect_timeout_ms=1000};
  char error[256]; hero_e2_agent *agent=hero_e2_start(&config,error,sizeof(error));
  if(!agent) { fprintf(stderr,"%s\n",error); return 0; }
  hero_e2_status state;
  int registered=hero_e2_wait(agent,mode==2 ? 200 : 3000,&state);
  int ok=(mode==0 || mode==4 ? registered && state.accepted_function==2 :
    mode==5 ? !registered && state.state==HERO_E2_SETUP_FAILED :
    mode==1 ? !registered && state.state==HERO_E2_SETUP_FAILED :
    mode==2 ? !registered && state.state==HERO_E2_SETUP_PENDING :
    !registered && state.state==HERO_E2_TRANSPORT_FAILED);
  if(mode==4) {
    // Trigger graceful peer shutdown after a successful setup and observe loss.
    pthread_mutex_lock(&f.mutex); f.release=1; pthread_cond_signal(&f.condition); pthread_mutex_unlock(&f.mutex);
    pthread_join(f.thread,NULL); close(f.fd); f.fd=-1;
    struct timespec delay={.tv_nsec=10000000};
    for(int i=0;i<200;i++) {
      state=hero_e2_get_status(agent);
      if(state.state==HERO_E2_TRANSPORT_FAILED) break;
      nanosleep(&delay,NULL);
    }
    ok=ok && state.state==HERO_E2_TRANSPORT_FAILED && f.valid_request;
  }
  hero_e2_stop(agent);
  if(mode!=3 && mode!=4) {
    pthread_mutex_lock(&f.mutex); f.release=1; pthread_cond_signal(&f.condition); pthread_mutex_unlock(&f.mutex);
    pthread_join(f.thread,NULL);
    ok=ok && f.valid_request;
  }
  if(f.fd>=0) close(f.fd);
  pthread_mutex_destroy(&f.mutex); pthread_cond_destroy(&f.condition);
  int after=descriptor_count();
  printf("FIXTURE mode=%d state=%d registered=%d fd_before=%d fd_after=%d result=%s\n",mode,state.state,registered,before,after,ok && before==after ? "PASS" : "FAIL");
  return ok && before==after;
}
int main(int argc,char **argv) {
  if(argc!=2) return 2;
  for(int repeat=0;repeat<2;repeat++) for(int mode=0;mode<6;mode++) if(!test(mode,argv[1])) return 1;
  puts("PASS: repeated setup success, encoded E2 Setup Failure, unanswered setup timeout, unavailable endpoint, peer shutdown, missing KPM acceptance, descriptor cleanup");
  return 0;
}
