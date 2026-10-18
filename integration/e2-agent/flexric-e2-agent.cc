#include "flexric-e2-agent.h"
#include "kpm/lena-measurement-provider.h"
#include "ns3/node.h"
#include "ns3/nr-mac-scheduler-ns3.h"
#include "ns3/simulator.h"
#include "sm/agent_if/read/sm_ag_if_rd.h"
#include "sm/kpm_sm/kpm_sm_v03.00/ie/kpm_data_ie.h"
#include "sm/rc_sm/ie/rc_data_ie.h"
#include "control/private-rc-extension.h"
#include "e2-agent/private-rc-validation.h"
#include <ns3/integer.h>
#include <chrono>
#include <condition_variable>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <utility>
namespace hero::oran {
bool detail::ValidatePrivateRcControlRequest(const rc_ctrl_req_data_t* request,
                                              std::int64_t& bwp,
                                              std::int64_t& maxDlMcs) {
  bwp=-1; maxDlMcs=-1;
  if(!request || request->hdr.format!=FORMAT_2_E2SM_RC_CTRL_HDR ||
     request->hdr.frmt_2.ue_id || request->hdr.frmt_2.ric_ctrl_dec ||
     request->msg.format!=FORMAT_2_E2SM_RC_CTRL_MSG ||
     request->msg.frmt_2.sz_seq_ctrl_sma!=1 || !request->msg.frmt_2.action) return false;
  const auto& style=request->msg.frmt_2.action[0];
  if(style.ctrl_style!=HERO_PRIVATE_RC_STYLE_ID || style.sz_seq_ctrl_act!=1 || !style.seq_ctrl_act) return false;
  const auto& action=style.seq_ctrl_act[0];
  if(action.ctrl_act_id!=HERO_PRIVATE_RC_ACTION_SET_MAX_DL_MCS || !action.ctrl_msg_frmt_1 ||
     action.ctrl_msg_frmt_1->sz_ran_param!=2 || !action.ctrl_msg_frmt_1->ran_param) return false;
  bool haveBwp=false,haveMcs=false;
  for(std::size_t i=0;i<action.ctrl_msg_frmt_1->sz_ran_param;i++) {
    const auto& parameter=action.ctrl_msg_frmt_1->ran_param[i];
    if(parameter.ran_param_val.type!=ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE ||
       !parameter.ran_param_val.flag_true || parameter.ran_param_val.flag_true->type!=INTEGER_RAN_PARAMETER_VALUE) return false;
    const auto value=parameter.ran_param_val.flag_true->int_ran;
    if(parameter.ran_param_id==HERO_PRIVATE_RC_PARAM_BWP_INDEX && !haveBwp) { haveBwp=true; bwp=value; }
    else if(parameter.ran_param_id==HERO_PRIVATE_RC_PARAM_MAX_DL_MCS && !haveMcs) { haveMcs=true; maxDlMcs=value; }
    else return false;
  }
  return haveBwp && haveMcs && bwp==0 && maxDlMcs>=0 && maxDlMcs<=30;
}
namespace {
constexpr const char* kDlName="HERO.DL.PDCP.SDU.Throughput.Mbps";
constexpr const char* kUlName="HERO.UL.PDCP.SDU.Throughput.Mbps";
struct ControlCompletion {
  std::mutex mutex;
  std::condition_variable ready;
  bool done{},cancelled{},applied{};
  std::int64_t effectiveMcs{-1};
};
void FillRcOutcome(e2sm_rc_ctrl_out_t* out,bool applied,std::int64_t effectiveMcs) {
  out->format=FORMAT_3_E2SM_RC_CTRL_OUT;
  out->frmt_3.sz_seq_ran_param=2;
  out->frmt_3.ran_param=static_cast<seq_ran_param_t*>(std::calloc(2,sizeof(seq_ran_param_t)));
  const std::uint32_t ids[2]={HERO_PRIVATE_RC_OUTCOME_APPLIED,HERO_PRIVATE_RC_OUTCOME_EFFECTIVE_MAX_DL_MCS};
  for(std::size_t i=0;i<2;i++) {
    auto& parameter=out->frmt_3.ran_param[i];
    parameter.ran_param_id=ids[i];
    parameter.ran_param_val.type=ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
    parameter.ran_param_val.flag_true=static_cast<ran_parameter_value_t*>(std::calloc(1,sizeof(ran_parameter_value_t)));
    parameter.ran_param_val.flag_true->type=i==0?BOOLEAN_RAN_PARAMETER_VALUE:INTEGER_RAN_PARAMETER_VALUE;
    if(i==0) parameter.ran_param_val.flag_true->bool_ran=applied;
    else parameter.ran_param_val.flag_true->int_ran=effectiveMcs;
  }
}
byte_array_t stringBytes(const char* text) {
  const auto len=std::strlen(text);
  auto* bytes=static_cast<std::uint8_t*>(std::malloc(len));
  std::memcpy(bytes,text,len);
  return {len,bytes};
}
int ApplyPrivateRcControl(void* context,const void* requestPointer,void* outcomePointer) {
  auto* owner=static_cast<FlexricE2Agent::ControlContext*>(context);
  auto* outcome=static_cast<e2sm_rc_ctrl_out_t*>(outcomePointer);
  if(!outcome) return 0;
  bool requestValid=false;
  std::int64_t requestedBwp=-1,requestedMcs=-1;
  auto* request=static_cast<const rc_ctrl_req_data_t*>(requestPointer);
  requestValid=owner && owner->gnb && detail::ValidatePrivateRcControlRequest(request,requestedBwp,requestedMcs);

  auto completion=std::make_shared<ControlCompletion>();
  if(requestValid) {
    const auto nodeId=owner->node.simulationNodeId;
    auto* gnb=owner->gnb;
    auto task=[completion,gnb,requestedMcs,nodeId,gnbId=owner->node.gnbId] {
      std::unique_lock lock(completion->mutex);
      if(completion->cancelled) return;
      ns3::Ptr<ns3::NrMacScheduler> scheduler=gnb->GetScheduler(0);
      auto concrete=ns3::DynamicCast<ns3::NrMacSchedulerNs3>(scheduler);
      ns3::IntegerValue current;
      bool applied=concrete && concrete->SetAttributeFailSafe("MaxDlMcs",ns3::IntegerValue(requestedMcs)) &&
        concrete->GetAttributeFailSafe("MaxDlMcs",current) && current.Get()==requestedMcs;
      completion->applied=applied;
      completion->effectiveMcs=applied?current.Get():-1;
      completion->done=true;
      std::cout<<"[HERO RC PRIVATE] gnb_id="<<gnbId<<" sim_node="<<nodeId<<" bwp=0 operation=SetMaxDlMcs requested="
        <<requestedMcs<<" applied="<<applied<<" effective="<<completion->effectiveMcs
        <<" sim_time_ns="<<ns3::Simulator::Now().GetNanoSeconds()<<std::endl;
      lock.unlock(); completion->ready.notify_all();
    };
    {
      std::lock_guard lock(owner->queueMutex);
      if(owner->closed) requestValid=false;
      else owner->pending.emplace_back(std::move(task));
    }
    if(requestValid) {
      std::unique_lock lock(completion->mutex);
      if(!completion->ready.wait_for(lock,std::chrono::seconds(3),[&]{return completion->done;})) {
        completion->cancelled=true;
        std::cerr<<"[HERO RC PRIVATE] gnb_id="<<owner->node.gnbId<<" dispatch timeout; control cancelled without scheduler change\n";
      }
    }
  }
  FillRcOutcome(outcome,requestValid && completion->applied,requestValid?completion->effectiveMcs:-1);
  if(!requestValid) std::cerr<<"[HERO RC PRIVATE] gnb_id="<<(owner?owner->node.gnbId:0)<<" rejected malformed/unsupported private control request\n";
  return 1;
}
int ReadNativeKpmIndication(void* context,void* requestPtr) {
  auto* reportContext=static_cast<FlexricE2Agent::KpmReadContext*>(context);
  auto* provider=reportContext ? reportContext->provider : nullptr;
  auto* request=static_cast<kpm_rd_ind_data_t*>(requestPtr);
  if(!provider || !request || !request->act_def || request->act_def->type!=FORMAT_1_ACTION_DEFINITION) return 0;
  const auto& action=request->act_def->frm_1;
  if(action.meas_info_lst_len==0 || action.meas_info_lst_len>2 || !action.meas_info_lst || !action.gran_period_ms) return 0;
  auto sample=provider->LatestNativeSnapshot();
  if(!sample.sequence || sample.records.empty() || sample.endSimulationNs<=sample.beginSimulationNs) return 0;
  const auto elapsed=sample.endSimulationNs-sample.beginSimulationNs;
  if(elapsed!=static_cast<std::int64_t>(action.gran_period_ms)*1'000'000) {
    std::cerr<<"[HERO KPM] suppress report: provider interval_ns="<<elapsed
      <<" differs from requested granularity_ms="<<action.gran_period_ms<<'\n';
    return 0;
  }
  if(sample.sequence<=reportContext->lastSequence) return 0;
  const auto& cell=sample.records.front();
  const double seconds=elapsed*1e-9;
  bool requestedDl[2]={false,false},requestedUl[2]={false,false};
  for(std::size_t i=0;i<action.meas_info_lst_len;i++) {
    const auto& requested=action.meas_info_lst[i];
    if(requested.meas_type.type!=0 || requested.label_info_lst_len!=1 ||
       !requested.label_info_lst || !requested.label_info_lst[0].noLabel ||
       *requested.label_info_lst[0].noLabel!=TRUE_ENUM_VALUE) return 0;
    const auto& name=requested.meas_type.name;
    const bool dl=name.buf && name.len==std::strlen(kDlName) && std::memcmp(name.buf,kDlName,name.len)==0;
    const bool ul=name.buf && name.len==std::strlen(kUlName) && std::memcmp(name.buf,kUlName,name.len)==0;
    if((!dl && !ul) || (dl && requestedDl[0]) || (ul && requestedUl[0])) return 0;
    requestedDl[i]=dl;
    requestedUl[i]=ul;
  }
  request->ind.hdr.type=FORMAT_1_INDICATION_HEADER;
  request->ind.hdr.kpm_ric_ind_hdr_format_1.collectStartTime=sample.collectStartTimeUs;
  request->ind.msg.type=FORMAT_1_INDICATION_MESSAGE;
  auto& msg=request->ind.msg.frm_1;
  msg.meas_data_lst_len=1;
  msg.meas_data_lst=static_cast<meas_data_lst_t*>(std::calloc(1,sizeof(meas_data_lst_t)));
  msg.meas_data_lst[0].meas_record_len=action.meas_info_lst_len;
  msg.meas_data_lst[0].meas_record_lst=static_cast<meas_record_lst_t*>(std::calloc(action.meas_info_lst_len,sizeof(meas_record_lst_t)));
  msg.meas_info_lst_len=action.meas_info_lst_len;
  msg.meas_info_lst=static_cast<meas_info_format_1_lst_t*>(std::calloc(action.meas_info_lst_len,sizeof(meas_info_format_1_lst_t)));
  msg.gran_period_ms=static_cast<std::uint32_t*>(std::malloc(sizeof(std::uint32_t)));
  *msg.gran_period_ms=action.gran_period_ms;
  for(std::size_t i=0;i<action.meas_info_lst_len;i++) {
    const bool dl=requestedDl[i];
    auto& info=msg.meas_info_lst[i];
    info.meas_type.type=static_cast<decltype(info.meas_type.type)>(0);
    info.meas_type.name=stringBytes(dl?kDlName:kUlName);
    info.label_info_lst_len=1;
    info.label_info_lst=static_cast<label_info_lst_t*>(std::calloc(1,sizeof(label_info_lst_t)));
    info.label_info_lst[0].noLabel=static_cast<enum_value_e*>(std::malloc(sizeof(enum_value_e)));
    *info.label_info_lst[0].noLabel=TRUE_ENUM_VALUE;
    auto& value=msg.meas_data_lst[0].meas_record_lst[i];
    value.value=REAL_MEAS_VALUE;
    value.real_val=(dl?cell.dl.rxPdcpSduBytes:cell.ul.rxPdcpSduBytes)*8e-6/seconds;
  }
  reportContext->lastSequence=sample.sequence;
  std::cout<<"[HERO KPM] encoded native sample sequence="<<sample.sequence
    <<" gnb_id="<<sample.node.gnbId
    <<" interval_sim_ns=["<<sample.beginSimulationNs<<','<<sample.endSimulationNs<<")"
    <<" cell="<<cell.cellId<<" dl_pdcp_sdu_mbps="<<cell.dl.rxPdcpSduBytes*8e-6/seconds
    <<" ul_pdcp_sdu_mbps="<<cell.ul.rxPdcpSduBytes*8e-6/seconds<<std::endl;
  return 1;
}
}
FlexricE2Agent::FlexricE2Agent(ns3::Ptr<ns3::NrGnbNetDevice> gnb, Endpoint endpoint,LenaMeasurementProvider* measurements,bool enablePrivateControl)
 : m_gnb(gnb),m_endpoint(std::move(endpoint)),m_measurements(measurements),m_enablePrivateControl(enablePrivateControl),m_kpmReadContext{measurements,0} { if(!gnb) throw std::invalid_argument("E2 agent needs a native gNB"); }
FlexricE2Agent::~FlexricE2Agent() { Stop(); }
void FlexricE2Agent::Start(const E2NodeContext& node) {
  if(m_agent) throw std::logic_error("E2 agent already started");
  if(!m_gnb->GetNode() || node.simulationNodeId!=m_gnb->GetNode()->GetId() || node.cellIds!=m_gnb->GetCellIds() || node.cellIds.empty())
    throw std::invalid_argument("E2 context does not match attached native gNB");
  m_controlContext.gnb=ns3::PeekPointer(m_gnb);
  m_controlContext.node=node;
  {
    std::lock_guard lock(m_controlContext.queueMutex);
    m_controlContext.closed=false;
    m_controlContext.pending.clear();
  }
  hero_e2_config config{m_endpoint.ip.c_str(),m_endpoint.pluginDirectory.c_str(),m_endpoint.port,node.mcc,node.mnc,node.mncDigits,node.gnbId,m_endpoint.connectTimeoutMs,
    &m_kpmReadContext,m_measurements?ReadNativeKpmIndication:nullptr,
    &m_controlContext,m_enablePrivateControl?ApplyPrivateRcControl:nullptr};
  char error[256]; m_agent=hero_e2_start(&config,error,sizeof(error));
  if(!m_agent) throw std::runtime_error(error);
  if(m_enablePrivateControl) ns3::Simulator::ScheduleWithContext(node.simulationNodeId,ns3::MilliSeconds(1),&FlexricE2Agent::PumpControlQueue,this);
}
void FlexricE2Agent::PumpControlQueue() {
  std::deque<std::function<void()>> tasks;
  {
    std::lock_guard lock(m_controlContext.queueMutex);
    tasks.swap(m_controlContext.pending);
  }
  for(auto& task:tasks) task();
  {
    std::lock_guard lock(m_controlContext.queueMutex);
    if(m_controlContext.closed) return;
  }
  ns3::Simulator::ScheduleWithContext(m_controlContext.node.simulationNodeId,ns3::MilliSeconds(1),&FlexricE2Agent::PumpControlQueue,this);
}
void FlexricE2Agent::Stop() {
  if(m_agent) { hero_e2_stop(m_agent); m_agent=nullptr; }
  if(m_enablePrivateControl) {
    std::lock_guard lock(m_controlContext.queueMutex);
    m_controlContext.closed=true;
    m_controlContext.pending.clear();
  }
}
bool FlexricE2Agent::WaitRegistered(std::uint32_t timeout) {
  if(!m_agent) throw std::logic_error("E2 agent not started");
  hero_e2_status status; return hero_e2_wait(m_agent,timeout,&status)!=0;
}
hero_e2_status FlexricE2Agent::Status() const {
  if(!m_agent) return {HERO_E2_STOPPED,0,0,"Agent stopped"};
  return hero_e2_get_status(m_agent);
}
}
