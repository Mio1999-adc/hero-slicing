#include "kpm/lena-measurement-provider.h"
#include "ns3/nr-gnb-phy.h"
#include "ns3/nr-gnb-mac.h"
#include "ns3/nr-gnb-rrc.h"
#include "ns3/nr-ue-rrc.h"
#include "ns3/nr-ue-phy.h"
#include "ns3/nr-radio-bearer-info.h"
#include "ns3/nr-pdcp.h"
#include "ns3/nr-pdcp-header.h"
#include "ns3/nr-rlc.h"
#include "ns3/nr-control-messages.h"
#include "ns3/object-ptr-container.h"
#include "ns3/node.h"
#include "ns3/simulator.h"
#include <algorithm>
#include <cmath>
#include <chrono>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>
#include <thread>
#include <tuple>

namespace hero::oran {
struct LenaMeasurementProvider::Impl {
  ns3::Ptr<ns3::NrGnbNetDevice> gnb;
  E2NodeContext node;
  ns3::Time interval;
  ns3::EventId event;
  std::thread::id owner=std::this_thread::get_id();
  bool running=false, everStarted=false;
  std::int64_t begin=0;
  std::uint64_t sequence=0, unmapped=0;
  std::uint8_t bwp=0;
  std::uint16_t cell=0;
  NativeMeasurementRecord total;
  using Key=std::tuple<std::uint64_t,std::uint16_t,std::uint64_t>;
  std::map<Key,NativeMeasurementRecord> records;
  std::map<std::uint16_t,std::uint64_t> epochs;
  std::map<std::uint64_t,ns3::Ptr<ns3::NrUeNetDevice>> ues;
  std::map<std::uint64_t,UeContext> metadata;
  std::set<ns3::Object*> bearerObjects;
  std::vector<std::function<void()>> disconnectors;
  SampleObserver observer;
  mutable std::mutex publishedMutex;
  NativeMeasurementSnapshot published;

  void CheckThread() const { if(std::this_thread::get_id()!=owner) throw std::logic_error("Measurement lifecycle requires simulation owning thread"); }
  template<class O,class C> void Connect(ns3::Ptr<O> object,const char *name,C callback) {
    if(!object || !object->TraceConnectWithoutContext(name,callback)) throw std::runtime_error(std::string("Native trace unavailable: ")+name);
    disconnectors.emplace_back([object,name=std::string(name),callback] { object->TraceDisconnectWithoutContext(name,callback); });
  }
  NativeMeasurementRecord* Resolve(std::uint64_t imsi,std::uint16_t rnti) {
    if(!running || !rnti || !gnb->GetRrc()->HasUeManager(rnti)) return nullptr;
    auto manager=gnb->GetRrc()->GetUeManager(rnti);
    if(!manager->GetImsi() || (imsi && manager->GetImsi()!=imsi)) return nullptr;
    imsi=manager->GetImsi();
    auto key=Key{imsi,rnti,epochs[rnti]};
    auto [it,inserted]=records.try_emplace(key);
    if(inserted) {
      it->second.cellId=cell; it->second.bwpIndex=bwp;
      it->second.contextGeneration=epochs[rnti];
      UeContext context{};
      context.imsi=imsi; context.cellId=cell; context.rnti=rnti; context.bwpIndex=bwp;
      if(auto meta=metadata.find(imsi);meta!=metadata.end()) {
        context.sliceId=meta->second.sliceId; context.sliceType=meta->second.sliceType;
        context.applicationType=meta->second.applicationType; context.qosProfile=meta->second.qosProfile;
        context.offeredRateBps=meta->second.offeredRateBps;
        context.requiredThroughputBps=meta->second.requiredThroughputBps;
        context.packetSizeBytes=meta->second.packetSizeBytes; context.packetIntervalUs=meta->second.packetIntervalUs;
        context.delayTargetMs=meta->second.delayTargetMs; context.reliabilityTarget=meta->second.reliabilityTarget;
      }
      it->second.ue=std::move(context);
    }
    return &it->second;
  }
  void NewUe(std::uint16_t observedCell,std::uint16_t rnti) {
    if(!running || observedCell!=cell) return;
    ++epochs[rnti];
    auto manager=gnb->GetRrc()->GetUeManager(rnti);
    Connect(manager,"DrbCreated",ns3::MakeCallback(&Impl::GnbBearer,this));
    GnbBearer(manager->GetImsi(),cell,rnti,0);
  }
  void Connection(std::uint64_t imsi,std::uint16_t observedCell,std::uint16_t rnti) {
    if(!running || observedCell!=cell) return;
    if(auto *r=Resolve(imsi,rnti)) r->connectedAtEnd=true;
    GnbBearer(imsi,observedCell,rnti,0);
  }
  void Release(std::uint64_t imsi,std::uint16_t observedCell,std::uint16_t rnti) {
    if(observedCell!=cell) return;
    for(auto &[key,r]:records) if(r.ue->imsi==imsi && r.ue->rnti==rnti) r.connectedAtEnd=false;
  }
  void GnbBearer(std::uint64_t imsi,std::uint16_t observedCell,std::uint16_t rnti,std::uint8_t) {
    if(!running || observedCell!=cell || !gnb->GetRrc()->HasUeManager(rnti)) return;
    auto manager=gnb->GetRrc()->GetUeManager(rnti);
    imsi=manager->GetImsi();
    ns3::ObjectPtrContainerValue bearers;
    manager->GetAttribute("DataRadioBearerMap",bearers);
    AttachBearers(bearers,imsi,rnti,false);
  }
  void UeBearer(std::uint64_t imsi,std::uint16_t observedCell,std::uint16_t rnti,std::uint8_t) {
    if(!running || observedCell!=cell || !ues.contains(imsi)) return;
    ns3::ObjectPtrContainerValue bearers;
    ues.at(imsi)->GetRrc()->GetAttribute("DataRadioBearerMap",bearers);
    AttachBearers(bearers,imsi,rnti,true);
  }
  void AttachBearers(const ns3::ObjectPtrContainerValue& bearers,std::uint64_t imsi,std::uint16_t rnti,bool ueSide) {
    auto *record=Resolve(imsi,rnti); if(!record) return;
    for(auto it=bearers.Begin();it!=bearers.End();++it) {
      auto bearer=ns3::DynamicCast<ns3::NrDataRadioBearerInfo>(it->second);
      if(!bearer || !bearer->m_rlc) continue;
      if(ns3::DynamicCast<ns3::NrRlcSm>(bearer->m_rlc)) throw std::runtime_error("Saturation RLC is not a real-traffic measurement source");
      // Bound identities are checked against the current native UE manager in every callback.
      if(bearerObjects.insert(ns3::PeekPointer(bearer->m_rlc)).second) {
      Connect(bearer->m_rlc,"TxPDU",ns3::MakeCallback(&Impl::PduTx,this).Bind(imsi,record->contextGeneration,ueSide,false));
      Connect(bearer->m_rlc,"RxPDU",ns3::MakeCallback(&Impl::PduRx,this).Bind(imsi,record->contextGeneration,ueSide,false));
      }
      if(bearer->m_pdcp) {
        if(bearerObjects.insert(ns3::PeekPointer(bearer->m_pdcp)).second) {
        Connect(bearer->m_pdcp,"TxPDU",ns3::MakeCallback(&Impl::PduTx,this).Bind(imsi,record->contextGeneration,ueSide,true));
        Connect(bearer->m_pdcp,"RxPDU",ns3::MakeCallback(&Impl::PduRx,this).Bind(imsi,record->contextGeneration,ueSide,true));
        }
        if(ueSide) record->uePdcpAttached=true;
        else record->gnbPdcpAttached=true;
      }
      if(ueSide) record->ueReceiverAttached=true;
    }
  }
  bool UeMatches(std::uint64_t imsi,std::uint16_t rnti) const {
    return ues.contains(imsi) && ues.at(imsi)->GetRrc()->GetCellId()==cell && ues.at(imsi)->GetRrc()->GetRnti()==rnti;
  }
  void PduTx(std::uint64_t imsi,std::uint64_t generation,bool ueSide,bool pdcp,std::uint16_t rnti,std::uint8_t,std::uint32_t size) {
    auto *record=Resolve(imsi,rnti);
    if(!record || record->contextGeneration!=generation || (ueSide && !UeMatches(imsi,rnti))) { if(running) ++unmapped; return; }
    for(auto *r:{record,&total}) {
      auto &d=ueSide ? r->ul : r->dl;
      if(pdcp) {
        auto headerSize=ns3::NrPdcpHeader().GetSerializedSize();
        if(size<headerSize) throw std::runtime_error("PDCP PDU smaller than native header");
        d.txPdcpPduBytes+=size; d.txPdcpSduBytes+=size-headerSize; ++d.txPdcpPackets;
      } else { d.txRlcPduBytes+=size; ++d.txRlcPackets; }
    }
  }
  void PduRx(std::uint64_t imsi,std::uint64_t generation,bool ueSide,bool pdcp,std::uint16_t rnti,std::uint8_t,std::uint32_t size,std::uint64_t delay) {
    auto *record=Resolve(imsi,rnti);
    if(!record || record->contextGeneration!=generation || (ueSide && !UeMatches(imsi,rnti))) { if(running) ++unmapped; return; }
    for(auto *r:{record,&total}) {
      auto &d=ueSide ? r->dl : r->ul;
      if(pdcp) {
        auto headerSize=ns3::NrPdcpHeader().GetSerializedSize();
        if(size<headerSize) throw std::runtime_error("PDCP PDU smaller than native header");
        d.rxPdcpPduBytes+=size; d.rxPdcpSduBytes+=size-headerSize; ++d.rxPdcpPackets; d.pdcpRxDelayNsSum+=delay;
      } else { d.rxRlcPduBytes+=size; ++d.rxRlcPackets; d.rlcRxDelayNsSum+=delay; }
    }
  }
  void TxControl(ns3::SfnSf,std::uint16_t observedCell,std::uint16_t,std::uint8_t,ns3::Ptr<const ns3::NrControlMessage> message) {
    if(!running || observedCell!=cell) return;
    // Installed DCI getters are non-const; casting here never changes message/state.
    auto mutableMessage=ns3::ConstCast<ns3::NrControlMessage>(message);
    std::shared_ptr<ns3::DciInfoElementTdma> dci;
    if(message->GetMessageType()==ns3::NrControlMessage::DL_DCI) dci=ns3::DynamicCast<ns3::NrDlDciMessage>(mutableMessage)->GetDciInfoElement();
    else if(message->GetMessageType()==ns3::NrControlMessage::UL_DCI) dci=ns3::DynamicCast<ns3::NrUlDciMessage>(mutableMessage)->GetDciInfoElement();
    if(!dci || dci->m_type!=ns3::DciInfoElementTdma::DATA || !dci->m_numSym || !dci->m_tbSize) return;
    // Initial-access DCI can carry temporary/RA RNTIs before the UE manager
    // exists. It is not attributable user scheduling for this provider.
    if(!gnb->GetRrc()->HasUeManager(dci->m_rnti) || !gnb->GetRrc()->GetUeManager(dci->m_rnti)->GetImsi()) return;
    auto phy=gnb->GetPhy(bwp);
    auto width=phy->GetNumRbPerRbg(), available=phy->GetRbNum();
    if(!width || dci->m_symStart+dci->m_numSym>phy->GetSymbolsPerSlot()) throw std::runtime_error("Invalid native DCI allocation dimensions");
    std::uint64_t prbs=0;
    for(std::size_t i=0;i<dci->m_rbgBitmask.size();++i) {
      if(dci->m_rbgBitmask[i]>1 || i*width>=available) throw std::runtime_error("DCI RBG mask outside native bandwidth");
      if(dci->m_rbgBitmask[i]) prbs+=std::min<std::uint64_t>(width,available-i*width);
    }
    auto *record=Resolve(0,dci->m_rnti);
    if(!record) { ++unmapped; return; }
    for(auto *r:{record,&total}) {
      auto &d=dci->m_format==ns3::DciInfoElementTdma::DL ? r->dl : r->ul;
      d.allocatedPrbGrantSum+=prbs; d.allocatedPrbSymbols+=prbs*dci->m_numSym;
      ++d.dataGrants; d.mcsSum+=dci->m_mcs; ++d.mcsSamples;
    }
    record->activeInWindow=true;
  }
  void RxControl(ns3::SfnSf,std::uint16_t observedCell,std::uint16_t,std::uint8_t,ns3::Ptr<const ns3::NrControlMessage> message) {
    if(!running || observedCell!=cell || message->GetMessageType()!=ns3::NrControlMessage::DL_CQI) return;
    auto cqi=ns3::DynamicCast<ns3::NrDlCqiMessage>(ns3::ConstCast<ns3::NrControlMessage>(message))->GetDlCqi();
    if(cqi.m_cqiType!=ns3::DlCqiInfo::WB) return;
    if(!gnb->GetRrc()->HasUeManager(cqi.m_rnti) || !gnb->GetRrc()->GetUeManager(cqi.m_rnti)->GetImsi()) return;
    auto *record=Resolve(0,cqi.m_rnti); if(!record) { ++unmapped; return; }
    for(auto *r:{record,&total}) { r->dlCqiSum+=cqi.m_wbCqi; ++r->dlCqiSamples; }
  }
  void Scheduling(bool uplink,::NrSchedulingCallbackInfo info) {
    // MAC reports Msg3/temporary-RNTI work during initial access too. These
    // are control-establishment allocations, not UE data grants in this series.
    if(!gnb->GetRrc()->HasUeManager(info.m_rnti) || !gnb->GetRrc()->GetUeManager(info.m_rnti)->GetImsi()) return;
    auto *record=Resolve(0,info.m_rnti); if(!record) { if(running) ++unmapped; return; }
    for(auto *r:{record,&total}) {
      auto &d=uplink ? r->ul : r->dl; ++d.macSchedulingEvents; d.macScheduledTbBytes+=info.m_tbSize;
    }
  }
  void UlSinr(std::uint64_t,ns3::SpectrumValue& values,ns3::SpectrumValue&) {
    if(!running) return;
    double sum=0; std::uint64_t count=0;
    for(auto it=values.ConstValuesBegin();it!=values.ConstValuesEnd();++it) if(*it>0 && std::isfinite(*it)) { sum+=*it; ++count; }
    if(count) { total.ul.sinrLinearSum+=sum/count; ++total.ul.sinrSamples; }
  }
  void DlSinr(std::uint64_t imsi,std::uint16_t observedCell,std::uint16_t rnti,double value,std::uint16_t) {
    if(!running || observedCell!=cell || !std::isfinite(value) || value<=0) return;
    // NrUePhy emits this trace during acquisition as well as after attachment.
    // Acquisition samples have no serving-cell UE context and are intentionally
    // excluded, rather than reported as unexplained measurement loss.
    if(!UeMatches(imsi,rnti)) return;
    auto *record=Resolve(imsi,rnti); if(!record) { ++unmapped; return; }
    for(auto *r:{record,&total}) { r->dl.sinrLinearSum+=value; ++r->dl.sinrSamples; }
  }
  void AttachRegisteredUes() {
    for(auto &[imsi,ue]:ues) {
      Connect(ue->GetRrc(),"DrbCreated",ns3::MakeCallback(&Impl::UeBearer,this));
      Connect(ue->GetPhy(bwp),"DlDataSinr",ns3::MakeCallback(&Impl::DlSinr,this).Bind(imsi));
      UeBearer(imsi,ue->GetRrc()->GetCellId(),ue->GetRrc()->GetRnti(),0);
    }
  }
  void Start() {
    CheckThread(); if(everStarted) throw std::logic_error("Provider is single-use; create a new one to restart");
    running=true; everStarted=true; begin=ns3::Simulator::Now().GetNanoSeconds();
    try {
      auto rrc=gnb->GetRrc();
      Connect(rrc,"NewUeContext",ns3::MakeCallback(&Impl::NewUe,this));
      Connect(rrc,"ConnectionEstablished",ns3::MakeCallback(&Impl::Connection,this));
      Connect(rrc,"HandoverEndOk",ns3::MakeCallback(&Impl::Connection,this));
      Connect(rrc,"NotifyConnectionRelease",ns3::MakeCallback(&Impl::Release,this));
      Connect(gnb->GetPhy(bwp),"GnbPhyTxedCtrlMsgsTrace",ns3::MakeCallback(&Impl::TxControl,this));
      Connect(gnb->GetPhy(bwp),"GnbPhyRxedCtrlMsgsTrace",ns3::MakeCallback(&Impl::RxControl,this));
      Connect(gnb->GetPhy(bwp),"UlSinrTrace",ns3::MakeCallback(&Impl::UlSinr,this));
      Connect(gnb->GetMac(bwp),"DlScheduling",ns3::MakeCallback(&Impl::Scheduling,this).Bind(false));
      Connect(gnb->GetMac(bwp),"UlScheduling",ns3::MakeCallback(&Impl::Scheduling,this).Bind(true));
      ns3::ObjectPtrContainerValue managers; rrc->GetAttribute("UeMap",managers);
      for(auto it=managers.Begin();it!=managers.End();++it) {
        auto manager=ns3::DynamicCast<ns3::NrUeManager>(it->second);
        NewUe(cell,manager->GetRnti());
      }
      AttachRegisteredUes();
      event=ns3::Simulator::Schedule(interval,&Impl::Tick,this);
    } catch(...) { Stop(); throw; }
  }
  void Tick() { Sample(); if(running) event=ns3::Simulator::Schedule(interval,&Impl::Tick,this); }
  void Sample() {
    CheckThread(); if(!running) return;
    auto end=ns3::Simulator::Now().GetNanoSeconds(); if(end<=begin) return;
    NativeMeasurementSnapshot snapshot{node,begin,end,++sequence,unmapped,{}};
    auto wallEnd=std::chrono::system_clock::now().time_since_epoch();
    snapshot.collectStartTimeUs=std::chrono::duration_cast<std::chrono::microseconds>(wallEnd).count()-(end-begin)/1000;
    total.availablePrbs=gnb->GetPhy(bwp)->GetRbNum();
    total.prbsPerRbg=gnb->GetPhy(bwp)->GetNumRbPerRbg();
    total.symbolsPerSlot=gnb->GetPhy(bwp)->GetSymbolsPerSlot();
    total.connectedUes=0; total.activeUes=0;
    total.gnbPdcpAttached=!records.empty(); total.uePdcpAttached=!records.empty();
    for(auto &[key,r]:records) {
      r.availablePrbs=total.availablePrbs; r.prbsPerRbg=total.prbsPerRbg;
      r.symbolsPerSlot=total.symbolsPerSlot;
      bool exists=gnb->GetRrc()->HasUeManager(r.ue->rnti);
      r.connectedAtEnd=exists && gnb->GetRrc()->GetUeManager(r.ue->rnti)->GetImsi()==r.ue->imsi && epochs[r.ue->rnti]==r.contextGeneration &&
        gnb->GetRrc()->GetUeManager(r.ue->rnti)->GetState()==ns3::NrUeManager::CONNECTED_NORMALLY;
      total.connectedUes+=r.connectedAtEnd; total.activeUes+=r.activeInWindow;
      total.gnbPdcpAttached=total.gnbPdcpAttached && r.gnbPdcpAttached;
      total.uePdcpAttached=total.uePdcpAttached && r.uePdcpAttached;
      total.ueReceiverAttached=total.ueReceiverAttached || r.ueReceiverAttached;
      snapshot.records.push_back(r);
      r.dl={}; r.ul={}; r.dlCqiSum=r.dlCqiSamples=0; r.activeInWindow=false;
    }
    snapshot.records.insert(snapshot.records.begin(),total);
    { std::lock_guard lock(publishedMutex); published=snapshot; }
    begin=end; unmapped=0; total.dl={}; total.ul={}; total.dlCqiSum=total.dlCqiSamples=0;
    if(observer) observer(snapshot);
  }
  void Stop() {
    CheckThread(); running=false; event.Cancel();
    for(auto it=disconnectors.rbegin();it!=disconnectors.rend();++it) (*it)();
    disconnectors.clear(); bearerObjects.clear();
  }
};
LenaMeasurementProvider::LenaMeasurementProvider(ns3::Ptr<ns3::NrGnbNetDevice> gnb,E2NodeContext node,ns3::Time interval):m_impl(std::make_unique<Impl>()) {
  if(!gnb || !gnb->GetNode() || interval.GetNanoSeconds()<=0 || node.simulationNodeId!=gnb->GetNode()->GetId() || node.cellIds!=gnb->GetCellIds()) throw std::invalid_argument("Invalid measurement interval/native gNB context");
  // RRC bearer traces lack BWP identity: avoid inventing attribution across BWPs.
  ns3::ObjectPtrContainerValue parts; gnb->GetAttribute("BandwidthPartMap",parts);
  if(parts.GetN()!=1 || parts.Begin()->first>255) throw std::invalid_argument("Phase 03 provider requires exactly one native BWP");
  m_impl->bwp=static_cast<std::uint8_t>(parts.Begin()->first);
  m_impl->cell=gnb->GetPhy(m_impl->bwp)->GetCellId();
  m_impl->gnb=gnb; m_impl->node=std::move(node); m_impl->interval=interval;
  m_impl->total.cellId=m_impl->cell; m_impl->total.bwpIndex=m_impl->bwp;
  m_impl->published.node=m_impl->node;
}
LenaMeasurementProvider::~LenaMeasurementProvider() { m_impl->Stop(); }
void LenaMeasurementProvider::RegisterUe(ns3::Ptr<ns3::NrUeNetDevice> ue, const UeContext& metadata) {
  m_impl->CheckThread(); if(m_impl->everStarted || !ue || ue->GetCcMapSize()!=1) throw std::invalid_argument("Register single-BWP UEs before provider Start");
  if(!m_impl->ues.emplace(ue->GetImsi(),ue).second) throw std::invalid_argument("Duplicate UE IMSI registration");
  m_impl->metadata.emplace(ue->GetImsi(), metadata);
}
void LenaMeasurementProvider::SetObserver(SampleObserver observer) { m_impl->CheckThread(); m_impl->observer=std::move(observer); }
void LenaMeasurementProvider::Start() { m_impl->Start(); }
void LenaMeasurementProvider::Stop() { m_impl->Stop(); }
void LenaMeasurementProvider::SampleNow() { m_impl->Sample(); }
NativeMeasurementSnapshot LenaMeasurementProvider::LatestNativeSnapshot() const { std::lock_guard lock(m_impl->publishedMutex); return m_impl->published; }
MeasurementSnapshot LenaMeasurementProvider::LatestSnapshot() const {
  auto native=LatestNativeSnapshot();
  MeasurementSnapshot result{native.beginSimulationNs,native.endSimulationNs,native.sequence,{},native.node,native.collectStartTimeUs};
  double seconds=(native.endSimulationNs-native.beginSimulationNs)*1e-9;
  for(const auto &r:native.records) {
    auto add=[&](std::string name,std::string unit,std::optional<double> value) { result.measurements.push_back({std::move(name),std::move(unit),r.ue,value,r.cellId,r.bwpIndex}); };
    for(bool uplink:{false,true}) {
      const auto &d=uplink?r.ul:r.dl; std::string prefix=uplink?"ul.":"dl.";
      bool receiver=uplink?r.gnbPdcpAttached:r.uePdcpAttached;
      add(prefix+"pdcp_delivered_sdu_throughput_mbps","Mbit/s",receiver && seconds>0?std::optional<double>(d.rxPdcpSduBytes*8e-6/seconds):std::nullopt);
      add(prefix+"allocated_prb_grant_sum","PRB-grants",static_cast<double>(d.allocatedPrbGrantSum));
      add(prefix+"allocated_prb_symbols","PRB-symbols",static_cast<double>(d.allocatedPrbSymbols));
      add(prefix+"mcs_mean","index",d.mcsSamples?std::optional<double>(double(d.mcsSum)/d.mcsSamples):std::nullopt);
      add(prefix+"sinr_mean_linear","ratio",d.sinrSamples?std::optional<double>(d.sinrLinearSum/d.sinrSamples):std::nullopt);
      add(prefix+"pdcp_received_pdu_bytes","bytes",receiver?std::optional<double>(d.rxPdcpPduBytes):std::nullopt);
      add(prefix+"pdcp_received_packets","PDUs",receiver?std::optional<double>(d.rxPdcpPackets):std::nullopt);
      add(prefix+"pdcp_rx_delay_mean","ns",d.rxPdcpPackets?std::optional<double>(double(d.pdcpRxDelayNsSum)/d.rxPdcpPackets):std::nullopt);
      add(prefix+"rlc_rx_delay_mean","ns",d.rxRlcPackets?std::optional<double>(double(d.rlcRxDelayNsSum)/d.rxRlcPackets):std::nullopt);
      add(prefix+"rlc_buffer_occupancy","bytes",std::nullopt);
      add(prefix+"mac_buffer_occupancy","bytes",std::nullopt);
    }
    add("dl.cqi_wideband_mean","index",r.dlCqiSamples?std::optional<double>(double(r.dlCqiSum)/r.dlCqiSamples):std::nullopt);
    add("available_prbs","PRBs",r.availablePrbs);
    add("active_scheduled_ues","UEs",r.ue?double(r.activeInWindow):double(r.activeUes));
    add("connected_ues_at_end","UEs",r.ue?double(r.connectedAtEnd):double(r.connectedUes));
  }
  return result;
}
}
