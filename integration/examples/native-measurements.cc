#include "kpm/lena-measurement-provider.h"
#include "kpm/measurement-csv.h"
#include "e2-agent/flexric-e2-agent.h"
#include "slicing/nr-mac-scheduler-hero-slice-pf.h"
#include "learning/ppo-action-client.h"
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/mobility-module.h"
#include "ns3/nr-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include <iostream>
#include <stdexcept>
#include <thread>
#include <atomic>
#include <csignal>
#include <limits>
#include <cmath>
#include <algorithm>
#include <numeric>
#include <array>
#include <map>
#include <memory>
#include <iomanip>

using namespace ns3;
using namespace hero::oran;

namespace {
volatile std::sig_atomic_t gStopSignal = 0;
void AlternateLiveTrafficRate(Ptr<UdpClient> client, bool burst) {
  // Continuous dashboard sessions use a staggered burst profile instead of
  // saturating every UE with a constant 5/7 ms packet stream.
  client->SetAttribute("Interval", TimeValue(MilliSeconds(burst ? 6 : 24)));
  Simulator::Schedule(MilliSeconds(700), [client, burst] {
    AlternateLiveTrafficRate(client, !burst);
  });
}
void RequestStop(int signal) { gStopSignal = signal; }
void PollStopSignal() {
  if (gStopSignal) {
    std::cerr << "[HERO] Stop requested; closing measurement providers and E2 agents.\n";
    Simulator::Stop();
    return;
  }
  Simulator::Schedule(MilliSeconds(100), &PollStopSignal);
}
struct SliceProfile {
  std::uint8_t id{};
  std::string name, application, bearer;
  std::uint32_t packetBytes{};
  std::uint64_t intervalUs{}, offeredRateBps{}, requiredThroughputBps{};
  double delayTargetMs{}, reliabilityTarget{};
};
SliceProfile ProfileFor(std::uint8_t id, std::uint32_t embbPacketBytes, std::uint64_t embbRateBps,
                        std::uint64_t embbRequiredBps,
                        std::uint32_t urllcPacketBytes, std::uint64_t urllcIntervalUs,
                        std::uint64_t urllcRequiredBps, double urllcDelayTargetMs, double urllcReliabilityTarget,
                        std::uint32_t mmtcPacketBytes, std::uint64_t mmtcIntervalUs,
                        std::uint64_t mmtcRequiredBps, double mmtcDelayTargetMs, double mmtcReliabilityTarget) {
  if(id==1) return {1,"eMBB","video","NGBR_VIDEO_TCP_DEFAULT",embbPacketBytes,
    static_cast<std::uint64_t>((static_cast<long double>(embbPacketBytes)*8.0L*1'000'000.0L)/embbRateBps),
    embbRateBps,embbRequiredBps,0.0,0.0};
  if(id==2) return {2,"URLLC","industrial-control","DGBR_DISCRETE_AUT_SMALL",urllcPacketBytes,
    urllcIntervalUs,static_cast<std::uint64_t>(urllcPacketBytes)*8'000'000ULL/urllcIntervalUs,urllcRequiredBps,urllcDelayTargetMs,urllcReliabilityTarget};
  return {3,"mMTC","sensor-reporting","NGBR_MC_DATA",mmtcPacketBytes,mmtcIntervalUs,
    static_cast<std::uint64_t>(mmtcPacketBytes)*8'000'000ULL/mmtcIntervalUs,mmtcRequiredBps,mmtcDelayTargetMs,mmtcReliabilityTarget};
}
}

int main(int argc,char **argv) {
  std::uint32_t ueCount=3, gnbCount=1, sampleMs=100;
  std::uint32_t embbUes=0,urllcUes=0,mmtcUes=0,seed=1,rngRun=1;
  std::uint32_t embbPacketBytes=1200,urllcPacketBytes=128,mmtcPacketBytes=64;
  std::uint64_t embbRateBps=10'000'000,urllcIntervalUs=10'000,mmtcIntervalUs=1'000'000,embbRequiredBps=10'000'000;
  std::uint64_t urllcRequiredBps=0,mmtcRequiredBps=0;
  double urllcDelayTargetMs=10.0,urllcReliabilityTarget=0.9999,mmtcDelayTargetMs=1000.0,mmtcReliabilityTarget=0.99;
  std::string sliceAssignment="off";
  bool enableSliceEnforcement=false;
  double embbRatio=0.34, urllcRatio=0.33, mmtcRatio=0.33;
  bool enablePpo=false;
  std::string ppoTier="local",ppoMode="train",ppoFormulation="baseline",ppoCheckpointDir="integration/build/ppo-checkpoints",ppoExperienceFile="";
  std::uint32_t ppoUpdateEvery=8,ppoMaxEpisodes=100,ppoEpisodeTransitions=32,ppoSeed=1;
  double ppoRewardThroughput=1.0,ppoRewardLatency=0.0,ppoRewardReliability=0.0,ppoRewardSla=0.0,ppoRewardControlCost=0.0;
  double ppoRewardQuality=0.50,ppoRewardAdequacy=0.20,ppoRewardUsefulEfficiency=0.15,ppoRewardFairness=0.10,ppoRewardActionStability=0.05;
  double embbProbability=0.4,urllcProbability=0.3,mmtcProbability=0.3;
  int failE2GnbIndex=-1;
  std::string csv="measurements.csv",ricIp="127.0.0.1",plugins=HERO_KPM_PLUGIN_DIR;
  std::uint16_t ricPort=36421;
  double duration=2,gnbSpacingM=500.0,ueDistanceM=20.0;
  bool collect=true,enableE2=false,enableControl=false,realtime=false,allowPartialE2=false;
  CommandLine cmd;
  cmd.AddValue("ues","Total number of native UEs (1..32)",ueCount);
  cmd.AddValue("gnbs","Number of native gNBs (1..15)",gnbCount);
  cmd.AddValue("gnbSpacingM","Distance between adjacent gNBs in metres",gnbSpacingM);
  cmd.AddValue("ueDistanceM","Distance from each UE to its assigned nearest gNB in metres",ueDistanceM);
  cmd.AddValue("sampleMs","Sampling interval in simulated milliseconds",sampleMs);
  cmd.AddValue("sliceAssignment","UE slice mapping: off (baseline), explicit, or random",sliceAssignment);
  cmd.AddValue("enableSliceEnforcement","Enable opt-in per-slice OFDMA PF RBG-symbol budgets",enableSliceEnforcement);
  cmd.AddValue("embbRatio","Initial eMBB RBG-symbol budget fraction (sum with other ratios to 1)",embbRatio);
  cmd.AddValue("urllcRatio","Initial URLLC RBG-symbol budget fraction",urllcRatio);
  cmd.AddValue("mmtcRatio","Initial mMTC RBG-symbol budget fraction",mmtcRatio);
  cmd.AddValue("enablePpo","Enable live PPO slice-ratio control using measured native LENA windows",enablePpo);
  cmd.AddValue("ppoTier","PPO controller tier: local or regional",ppoTier);
  cmd.AddValue("ppoFormulation","Local PPO formulation: baseline or ue-aware (regional requires baseline)",ppoFormulation);
  cmd.AddValue("ppoMode","PPO mode: train online or infer from checkpoints",ppoMode);
  cmd.AddValue("ppoCheckpointDir","PPO checkpoint output/input directory",ppoCheckpointDir);
  cmd.AddValue("ppoExperienceFile","JSONL path for actual PPO state/action/reward transitions (empty uses checkpoint directory)",ppoExperienceFile);
  cmd.AddValue("ppoUpdateEvery","Online PPO transitions per policy update",ppoUpdateEvery);
  cmd.AddValue("ppoMaxEpisodes","Maximum local PPO training episodes per gNB (training stops after this many episodes)",ppoMaxEpisodes);
  cmd.AddValue("ppoEpisodeTransitions","Measured control transitions per learning episode; no ns-3 reset is performed",ppoEpisodeTransitions);
  cmd.AddValue("ppoSeed","PPO initialization seed",ppoSeed);
  cmd.AddValue("ppoRewardThroughput","Experimental throughput reward weight",ppoRewardThroughput);
  cmd.AddValue("ppoRewardLatency","Experimental delay reward weight",ppoRewardLatency);
  cmd.AddValue("ppoRewardReliability","Reliability reward weight (must be zero until telemetry exists)",ppoRewardReliability);
  cmd.AddValue("ppoRewardSla","Experimental throughput-SLA reward weight",ppoRewardSla);
  cmd.AddValue("ppoRewardControlCost","Experimental ratio-change cost weight",ppoRewardControlCost);
  cmd.AddValue("ppoRewardQuality","UE-aware reward weight Q: UE QoS satisfaction",ppoRewardQuality);
  cmd.AddValue("ppoRewardAdequacy","UE-aware reward weight A: slice budget adequacy",ppoRewardAdequacy);
  cmd.AddValue("ppoRewardUsefulEfficiency","UE-aware reward weight E: useful MAC efficiency proxy",ppoRewardUsefulEfficiency);
  cmd.AddValue("ppoRewardFairness","UE-aware reward weight F: normalized Jain UE fairness",ppoRewardFairness);
  cmd.AddValue("ppoRewardActionStability","UE-aware reward weight H: applied ratio change penalty",ppoRewardActionStability);
  cmd.AddValue("embbUes","eMBB UE count in explicit mapping",embbUes);
  cmd.AddValue("urllcUes","URLLC UE count in explicit mapping",urllcUes);
  cmd.AddValue("mmtcUes","mMTC UE count in explicit mapping",mmtcUes);
  cmd.AddValue("embbProbability","Random mapping probability for eMBB",embbProbability);
  cmd.AddValue("urllcProbability","Random mapping probability for URLLC",urllcProbability);
  cmd.AddValue("mmtcProbability","Random mapping probability for mMTC",mmtcProbability);
  cmd.AddValue("seed","ns-3 reproducible random seed for slice assignment",seed);
  cmd.AddValue("rngRun","ns-3 reproducible random run number",rngRun);
  cmd.AddValue("embbPacketBytes","eMBB application payload bytes",embbPacketBytes);
  cmd.AddValue("embbRateBps","eMBB offered payload rate per direction in bit/s",embbRateBps);
  cmd.AddValue("embbRequiredBps","eMBB diagnostic minimum-throughput target in bit/s (0 disables)",embbRequiredBps);
  cmd.AddValue("urllcRequiredBps","URLLC diagnostic minimum-throughput target in bit/s (0 disables)",urllcRequiredBps);
  cmd.AddValue("urllcDelayTargetMs","Configured URLLC application delay target in milliseconds",urllcDelayTargetMs);
  cmd.AddValue("urllcReliabilityTarget","Configured URLLC delivery-ratio target (0..1)",urllcReliabilityTarget);
  cmd.AddValue("urllcPacketBytes","URLLC application payload bytes",urllcPacketBytes);
  cmd.AddValue("urllcIntervalUs","URLLC packet interval in microseconds",urllcIntervalUs);
  cmd.AddValue("mmtcPacketBytes","mMTC application payload bytes",mmtcPacketBytes);
  cmd.AddValue("mmtcIntervalUs","mMTC packet interval in microseconds",mmtcIntervalUs);
  cmd.AddValue("mmtcRequiredBps","mMTC diagnostic minimum-throughput target in bit/s (0 disables)",mmtcRequiredBps);
  cmd.AddValue("mmtcDelayTargetMs","Configured mMTC reporting deadline in milliseconds",mmtcDelayTargetMs);
  cmd.AddValue("mmtcReliabilityTarget","Configured mMTC delivery-ratio target (0..1)",mmtcReliabilityTarget);
  cmd.AddValue("duration","Simulation seconds, at least 1.5; zero runs until SIGINT/SIGTERM",duration);
  cmd.AddValue("csv","CSV output path",csv);
  cmd.AddValue("collect","Attach measurement providers; false verifies observers have no radio effect",collect);
  cmd.AddValue("enableE2","Connect each native gNB independently to original FlexRIC KPM v3",enableE2);
  cmd.AddValue("enableControl","Advertise the private HERO RC MaxDlMcs extension on every E2 node",enableControl);
  cmd.AddValue("failE2GnbIndex","Testing only: direct this zero-based gNB agent to RIC port+2 (-1 disables)",failE2GnbIndex);
  cmd.AddValue("allowPartialE2","Continue radio simulation when a per-gNB E2 agent fails",allowPartialE2);
  cmd.AddValue("ricIp","Original FlexRIC Near-RT RIC IPv4 address",ricIp);
  cmd.AddValue("ricPort","Original FlexRIC SCTP port",ricPort);
  cmd.AddValue("plugins","Directory containing original FlexRIC service-model plugins",plugins);
  cmd.AddValue("realtime","Use ns-3 realtime simulator (required for SCTP reporting)",realtime);
  cmd.Parse(argc,argv);
  const bool ppoWeightsValid=std::isfinite(ppoRewardThroughput)&&ppoRewardThroughput>=0&&
      std::isfinite(ppoRewardLatency)&&ppoRewardLatency>=0&&std::isfinite(ppoRewardReliability)&&ppoRewardReliability==0&&
      std::isfinite(ppoRewardSla)&&ppoRewardSla>=0&&std::isfinite(ppoRewardControlCost)&&ppoRewardControlCost>=0;
  const std::array<double,5> boundedRewardWeights{ppoRewardQuality,ppoRewardAdequacy,ppoRewardUsefulEfficiency,ppoRewardFairness,ppoRewardActionStability};
  const bool boundedWeightsValid=std::all_of(boundedRewardWeights.begin(),boundedRewardWeights.end(),[](double weight){return std::isfinite(weight)&&weight>=0;})&&
      std::abs(std::accumulate(boundedRewardWeights.begin(),boundedRewardWeights.end(),0.0)-1.0)<=1e-9;
  if(enablePpo && (!enableSliceEnforcement || !collect || (ppoTier!="local"&&ppoTier!="regional") ||
      (ppoMode!="train"&&ppoMode!="infer") || !ppoUpdateEvery || !ppoMaxEpisodes || !ppoEpisodeTransitions || !ppoSeed ||
      (ppoTier=="regional"&&gnbCount>15) || !ppoWeightsValid ||
      (ppoFormulation!="baseline"&&ppoFormulation!="ue-aware") ||
      (ppoTier=="regional"&&ppoFormulation!="baseline") ||
      (ppoFormulation=="ue-aware"&&!boundedWeightsValid))) {
    std::cerr<<"PPO requires measurement collection, slice enforcement, supported tier/mode/formulation, positive update/seed, "
                "valid reward weights (UE-aware weights sum to one) and reliability weight=0.\n"; return 2;
  }
  if((sliceAssignment!="off" && sliceAssignment!="explicit" && sliceAssignment!="random") || !seed || !rngRun ||
     !embbPacketBytes || !urllcPacketBytes || !mmtcPacketBytes || !embbRateBps || !urllcIntervalUs || !mmtcIntervalUs ||
     embbPacketBytes>2000 || urllcPacketBytes>2000 || mmtcPacketBytes>2000 || embbRateBps>1'000'000'000ULL ||
     embbRequiredBps>1'000'000'000ULL || urllcRequiredBps>1'000'000'000ULL || mmtcRequiredBps>1'000'000'000ULL ||
     urllcIntervalUs>10'000'000ULL || mmtcIntervalUs>10'000'000ULL || seed>std::numeric_limits<std::uint32_t>::max() ||
     rngRun>std::numeric_limits<std::uint32_t>::max() ||
     !std::isfinite(embbProbability) || !std::isfinite(urllcProbability) || !std::isfinite(mmtcProbability) ||
     !std::isfinite(urllcDelayTargetMs) || urllcDelayTargetMs<=0 || !std::isfinite(mmtcDelayTargetMs) || mmtcDelayTargetMs<=0 ||
     !std::isfinite(urllcReliabilityTarget) || urllcReliabilityTarget<0 || urllcReliabilityTarget>1 ||
     !std::isfinite(mmtcReliabilityTarget) || mmtcReliabilityTarget<0 || mmtcReliabilityTarget>1) return 2;
  const double probabilitySum=embbProbability+urllcProbability+mmtcProbability;
  if(sliceAssignment=="explicit" && embbUes+urllcUes+mmtcUes!=ueCount) {
    std::cerr<<"Explicit eMBB+URLLC+mMTC counts must equal total UEs.\n"; return 2;
  }
  if(sliceAssignment=="random" && (embbProbability<0 || urllcProbability<0 || mmtcProbability<0 || std::abs(probabilitySum-1.0)>1e-9)) {
    std::cerr<<"Random slice probabilities must be nonnegative and sum to 1.\n"; return 2;
  }
  const hero::slicing::SliceBudgetManager::Ratios initialSliceRatios{embbRatio,urllcRatio,mmtcRatio};
  if(enableSliceEnforcement && (sliceAssignment=="off" || !hero::slicing::SliceBudgetManager::ValidRatios(initialSliceRatios))) {
    std::cerr<<"Slice enforcement requires sliceAssignment=explicit|random and finite ratios summing to 1.\n"; return 2;
  }
  if(enableControl && plugins==HERO_KPM_PLUGIN_DIR) plugins=HERO_PHASE06_PLUGIN_DIR;
  const bool runUntilStopped = duration == 0;
  if(!ueCount || ueCount>32 || !gnbCount || gnbCount>15 || ueCount<gnbCount || !sampleMs || (!runUntilStopped && duration<1.5) ||
     !std::isfinite(gnbSpacingM) || gnbSpacingM<50 || gnbSpacingM>5000 ||
     !std::isfinite(ueDistanceM) || ueDistanceM<1 || ueDistanceM>500 ||
     (gnbCount>1 && ueDistanceM>=gnbSpacingM/2) ||
     failE2GnbIndex < -1 || failE2GnbIndex >= static_cast<int>(gnbCount) ||
     (failE2GnbIndex>=0 && ricPort>65533) || (allowPartialE2 && !enableE2)) return 2;
  if((enableE2 && (!collect || !realtime)) || (enableControl && !enableE2)) { std::cerr<<"E2 requires measurement collection and realtime simulation; control requires E2\n"; return 2; }
  std::cout << "[HERO CONFIG] mode=" << (enableE2 ? "E2-connected" : "radio-only")
            << " gNBs=" << gnbCount << " UEs=" << ueCount << " sample_ms=" << sampleMs
            << " simulation=" << (runUntilStopped ? "until-stop" : std::to_string(duration)+"s")
            << " realtime=" << realtime << " collect=" << collect << std::endl;
  if(!enableE2) std::cout << "[HERO E2] disabled; this run will not attempt a Near-RT RIC association." << std::endl;
  else std::cout << "[HERO E2] target=" << ricIp << ':' << ricPort << " KPM_v3=enabled control=" << enableControl << std::endl;
  if(realtime) GlobalValue::Bind("SimulatorImplementationType",StringValue("ns3::RealtimeSimulatorImpl"));
  RngSeedManager::SetSeed(seed); RngSeedManager::SetRun(rngRun);
  std::vector<std::unique_ptr<LenaMeasurementProvider>> providers;
  std::vector<std::unique_ptr<FlexricE2Agent>> agents;
  std::atomic<bool> readDone=false;
  std::thread reader;
  std::unique_ptr<hero::learning::PpoActionClient> ppoClient;
  try {
    NodeContainer gnbs,ues,remote; gnbs.Create(gnbCount); ues.Create(ueCount); remote.Create(1);
    std::vector<std::uint8_t> ueSlices(ueCount,0);
    if(sliceAssignment=="explicit") {
      std::uint32_t index=0;
      for(std::uint32_t n=0;n<embbUes;n++) ueSlices[index++]=1;
      for(std::uint32_t n=0;n<urllcUes;n++) ueSlices[index++]=2;
      for(std::uint32_t n=0;n<mmtcUes;n++) ueSlices[index++]=3;
    } else if(sliceAssignment=="random") {
      auto random=CreateObject<UniformRandomVariable>(); random->SetStream(500);
      for(auto& slice:ueSlices) {
        const double choice=random->GetValue();
        slice=choice<embbProbability?1:(choice<embbProbability+urllcProbability?2:3);
      }
    }
    std::array<std::uint32_t,4> realized{};
    for(auto slice:ueSlices) if(slice) ++realized[slice];
    MobilityHelper mobility; mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel"); mobility.Install(gnbs); mobility.Install(ues);
    for(std::uint32_t i=0;i<gnbCount;i++) gnbs.Get(i)->GetObject<MobilityModel>()->SetPosition(Vector(gnbSpacingM*i,0,10));
    for(std::uint32_t i=0;i<ueCount;i++) {
      const auto owner=i%gnbCount, local=i/gnbCount;
      const auto perCell=(ueCount+gnbCount-1-owner)/gnbCount;
      const double angle=2.0*std::acos(-1.0)*local/std::max<std::uint32_t>(perCell,1);
      const double x=gnbSpacingM*owner+ueDistanceM*std::cos(angle);
      const double y=ueDistanceM*std::sin(angle);
      ues.Get(i)->GetObject<MobilityModel>()->SetPosition(Vector(x,y,1.5));
    }
    auto epc=CreateObject<NrPointToPointEpcHelper>();
    auto helper=CreateObject<NrHelper>(); helper->SetEpcHelper(epc);
    if(enableSliceEnforcement) helper->SetSchedulerTypeId(NrMacSchedulerHeroSlicePF::GetTypeId());
    helper->SetBeamformingHelper(CreateObject<IdealBeamformingHelper>());
    helper->SetPathlossAttribute("ShadowingEnabled",BooleanValue(false));
    CcBwpCreator creator; CcBwpCreator::SimpleOperationBandConf conf(3.5e9,20e6,1,BandwidthPartInfo::UMa);
    auto band=creator.CreateOperationBandContiguousCc(conf); helper->InitializeOperationBand(&band);
    auto bwps=CcBwpCreator::GetAllBwps({band});
    auto gnbDevices=helper->InstallGnbDevice(gnbs,bwps); auto ueDevices=helper->InstallUeDevice(ues,bwps);
    std::vector<std::shared_ptr<hero::slicing::SliceBudgetManager>> sliceManagers;
    std::vector<std::map<std::uint64_t,std::uint8_t>> cellSliceByImsi;
    if(enableSliceEnforcement) {
      for(std::uint32_t i=0;i<gnbCount;i++) {
        auto manager=std::make_shared<hero::slicing::SliceBudgetManager>(initialSliceRatios);
        auto scheduler=DynamicCast<NrMacSchedulerHeroSlicePF>(helper->GetScheduler(gnbDevices.Get(i),0));
        if(!scheduler) throw std::runtime_error("Configured HERO slice scheduler is not installed on gNB");
        scheduler->SetSliceBudgetManager(manager);
        sliceManagers.push_back(manager);
        cellSliceByImsi.emplace_back();
      }
      for(std::uint32_t u=0;u<ueCount;u++) {
        auto ue=DynamicCast<NrUeNetDevice>(ueDevices.Get(u));
        const auto owner=u%gnbCount;
        cellSliceByImsi.at(owner)[ue->GetImsi()]=ueSlices[u];
      }
    }
    for(std::uint32_t i=0;i<gnbCount;i++) helper->GetGnbPhy(gnbDevices.Get(i),0)->SetTxPower(30);
    helper->AssignStreams(gnbDevices,1); helper->AssignStreams(ueDevices,10);
    helper->UpdateDeviceConfigs(gnbDevices); helper->UpdateDeviceConfigs(ueDevices);
    InternetStackHelper internet; internet.Install(remote); internet.Install(ues);
    PointToPointHelper backhaul; backhaul.SetDeviceAttribute("DataRate",DataRateValue(DataRate("1Gb/s")));
    backhaul.SetDeviceAttribute("Mtu",UintegerValue(2500)); backhaul.SetChannelAttribute("Delay",TimeValue(MilliSeconds(1)));
    auto link=backhaul.Install(epc->GetPgwNode(),remote.Get(0));
    Ipv4AddressHelper addresses; addresses.SetBase("1.0.0.0","255.0.0.0"); auto linkAddresses=addresses.Assign(link);
    Ipv4StaticRoutingHelper routes;
    routes.GetStaticRouting(remote.Get(0)->GetObject<Ipv4>())->AddNetworkRouteTo(Ipv4Address("7.0.0.0"),Ipv4Mask("255.0.0.0"),1);
    auto ueAddresses=epc->AssignUeIpv4Address(ueDevices);
    ApplicationContainer sources,sinks;
    std::vector<Ptr<UdpServer>> dlServers,ulServers;
    for(std::uint32_t i=0;i<ueCount;i++) {
      routes.GetStaticRouting(ues.Get(i)->GetObject<Ipv4>())->SetDefaultRoute(epc->GetUeDefaultGatewayAddress(),1);
      UdpServerHelper dlSink(9000+i),ulSink(9100+i);
      auto dl=dlSink.Install(ues.Get(i)),ul=ulSink.Install(remote.Get(0));
      dlServers.push_back(DynamicCast<UdpServer>(dl.Get(0))); ulServers.push_back(DynamicCast<UdpServer>(ul.Get(0)));
      sinks.Add(dl); sinks.Add(ul);
      UdpClientHelper dlSource(ueAddresses.GetAddress(i),9000+i),ulSource(linkAddresses.GetAddress(1),9100+i);
      std::uint32_t dlMaxPackets=runUntilStopped?std::numeric_limits<std::uint32_t>::max():(enableE2?100000:100);
      std::uint32_t ulMaxPackets=runUntilStopped?std::numeric_limits<std::uint32_t>::max():(enableE2?100000:80);
      const auto profile=sliceAssignment=="off"?SliceProfile{}:ProfileFor(ueSlices[i],embbPacketBytes,embbRateBps,embbRequiredBps,
        urllcPacketBytes,urllcIntervalUs,urllcRequiredBps,urllcDelayTargetMs,urllcReliabilityTarget,
        mmtcPacketBytes,mmtcIntervalUs,mmtcRequiredBps,mmtcDelayTargetMs,mmtcReliabilityTarget);
      const auto headerBytes=SeqTsHeader().GetSerializedSize();
      dlSource.SetAttribute("MaxPackets",UintegerValue(dlMaxPackets));
      ulSource.SetAttribute("MaxPackets",UintegerValue(ulMaxPackets));
      if(sliceAssignment=="off") {
        dlSource.SetAttribute("PacketSize",UintegerValue(512)); dlSource.SetAttribute("Interval",TimeValue(MilliSeconds(5)));
        ulSource.SetAttribute("PacketSize",UintegerValue(256)); ulSource.SetAttribute("Interval",TimeValue(MilliSeconds(7)));
      } else {
        const auto appPacket=profile.packetBytes+headerBytes;
        const auto interval=MicroSeconds(profile.intervalUs);
        if(!runUntilStopped) {
          const auto count=static_cast<std::uint64_t>(std::ceil(duration*1'000'000.0/profile.intervalUs))+2;
          dlMaxPackets=ulMaxPackets=static_cast<std::uint32_t>(std::min<std::uint64_t>(count,std::numeric_limits<std::uint32_t>::max()));
        }
        dlSource.SetAttribute("MaxPackets",UintegerValue(dlMaxPackets));
        ulSource.SetAttribute("MaxPackets",UintegerValue(ulMaxPackets));
        dlSource.SetAttribute("PacketSize",UintegerValue(appPacket)); dlSource.SetAttribute("Interval",TimeValue(interval));
        ulSource.SetAttribute("PacketSize",UintegerValue(appPacket)); ulSource.SetAttribute("Interval",TimeValue(interval));
      }
      auto dlApps=dlSource.Install(remote.Get(0));
      auto ulApps=ulSource.Install(ues.Get(i));
      sources.Add(dlApps); sources.Add(ulApps);
      if(runUntilStopped && sliceAssignment=="off") {
        auto dlClient=DynamicCast<UdpClient>(dlApps.Get(0));
        auto ulClient=DynamicCast<UdpClient>(ulApps.Get(0));
        const auto dlPhase=MilliSeconds((i*173)%700);
        const auto ulPhase=MilliSeconds((i*173+350)%700);
        Simulator::Schedule(Seconds(0.3)+dlPhase,[dlClient]{ AlternateLiveTrafficRate(dlClient,false); });
        Simulator::Schedule(Seconds(0.3)+ulPhase,[ulClient]{ AlternateLiveTrafficRate(ulClient,false); });
      }
    }
    sources.Start(Seconds(0.3)); sinks.Start(Seconds(0.1));
    if (!runUntilStopped) { sources.Stop(Seconds(duration-0.2)); sinks.Stop(Seconds(duration)); }

    MeasurementCsvWriter writer(csv);
    struct Totals { std::uint64_t windows{},dlBytes{},ulBytes{},dlPrbs{},ulPrbs{},lastLiveLogNs{}; };
    std::vector<Totals> totals(gnbCount);
    std::vector<Ptr<NrGnbNetDevice>> nativeGnbs;
    std::vector<std::uint64_t> ppoObservedSequence(gnbCount,0);
    bool ppoLive=true;
    for(std::uint32_t i=0;i<gnbCount;i++) nativeGnbs.push_back(DynamicCast<NrGnbNetDevice>(gnbDevices.Get(i)));
    if(collect) {
      for(std::uint32_t i=0;i<gnbCount;i++) {
        E2NodeContext context{505,1,2,101+i,gnbs.Get(i)->GetId(),nativeGnbs[i]->GetCellIds()};
        auto provider=std::make_unique<LenaMeasurementProvider>(nativeGnbs[i],context,MilliSeconds(sampleMs));
        for(std::uint32_t u=0;u<ueCount;u++) {
          UeContext metadata{};
          if(sliceAssignment!="off") {
            const auto profile=ProfileFor(ueSlices[u],embbPacketBytes,embbRateBps,embbRequiredBps,
              urllcPacketBytes,urllcIntervalUs,urllcRequiredBps,urllcDelayTargetMs,urllcReliabilityTarget,
              mmtcPacketBytes,mmtcIntervalUs,mmtcRequiredBps,mmtcDelayTargetMs,mmtcReliabilityTarget);
            metadata.sliceId=profile.id; metadata.sliceType=profile.name; metadata.applicationType=profile.application;
            metadata.qosProfile=profile.bearer; metadata.offeredRateBps=profile.offeredRateBps;
            metadata.requiredThroughputBps=profile.requiredThroughputBps;
            metadata.packetSizeBytes=profile.packetBytes; metadata.packetIntervalUs=profile.intervalUs;
            metadata.delayTargetMs=profile.delayTargetMs; metadata.reliabilityTarget=profile.reliabilityTarget;
          }
          provider->RegisterUe(DynamicCast<NrUeNetDevice>(ueDevices.Get(u)),metadata);
        }
        provider->SetObserver([&,i](const NativeMeasurementSnapshot& sample) {
          auto& t=totals[i];
          if(sample.endSimulationNs<=sample.beginSimulationNs || sample.sequence!=++t.windows) throw std::runtime_error("Bad per-gNB measurement sampling window");
          std::optional<hero::slicing::SliceBudgetManager::WindowStats> sliceWindow;
          if(enableSliceEnforcement) sliceWindow=sliceManagers.at(i)->TakeWindowStats();
          writer.Write(sample,sliceWindow);
          const auto &cell=sample.records.front();
          t.dlBytes+=cell.dl.rxPdcpSduBytes; t.ulBytes+=cell.ul.rxPdcpSduBytes;
          t.dlPrbs+=cell.dl.allocatedPrbGrantSum; t.ulPrbs+=cell.ul.allocatedPrbGrantSum;
          if(enablePpo && ppoLive) {
            ppoObservedSequence[i]=sample.sequence;
            bool ready=ppoTier=="local";
            if(ppoTier=="regional")
              ready=std::all_of(ppoObservedSequence.begin(),ppoObservedSequence.end(),
                                [&](std::uint64_t sequence){return sequence==sample.sequence;});
            if(ready) {
              std::vector<std::uint32_t> ids;
              std::map<std::uint32_t,std::array<double,3>> ratios;
              for(std::uint32_t g=0;g<gnbCount;g++) {
                if(ppoTier=="local" && g!=i) continue;
                const auto id=101+g;
                ids.push_back(id);
                ratios.emplace(id,sliceManagers[g]->GetRatios());
              }
              const auto actions=ppoClient->Request(sample.sequence,sample.endSimulationNs,csv,ids,ratios);
              for(const auto& [id,action]:actions) {
                const auto index=id-101;
                if(index>=sliceManagers.size() || !sliceManagers[index]->SetRatios(action.ratios,action.policyVersion,"ppo-"+ppoTier))
                  throw std::runtime_error("PPO action rejected by the target gNB SliceBudgetManager");
                std::cout<<"[HERO PPO] applied tier="<<ppoTier<<" mode="<<ppoMode<<" gnb_id="<<id
                         <<" sample="<<sample.sequence<<" policy_version="<<action.policyVersion
                         <<" ratios="<<std::setprecision(8)<<action.ratios[0]<<','<<action.ratios[1]<<','<<action.ratios[2]
                         <<" target=next-scheduler-slots source=native-measurements"<<std::endl;
              }
            }
          }
          // Keep stdout useful and bounded: emit one native measurement summary
          // per simulated second (or per sample when the configured period is longer).
          const auto reportPeriodNs=std::max<std::uint64_t>(1'000'000'000ULL,
              static_cast<std::uint64_t>(sampleMs)*1'000'000ULL);
          if(sample.endSimulationNs-t.lastLiveLogNs>=reportPeriodNs) {
            const double seconds=static_cast<double>(sample.endSimulationNs-sample.beginSimulationNs)/1e9;
            const double dlMbps=seconds>0 ? cell.dl.rxPdcpSduBytes*8.0/seconds/1e6 : 0.0;
            const double ulMbps=seconds>0 ? cell.ul.rxPdcpSduBytes*8.0/seconds/1e6 : 0.0;
            std::cout << "[HERO KPM-LOCAL] gnb_id=" << sample.node.gnbId
                      << " window=" << sample.sequence << " sim_end_ms=" << sample.endSimulationNs/1'000'000
                      << " active_ues=" << cell.activeUes
                      << " connected_ues=" << cell.connectedUes << " dl_pdcp_mbps=" << dlMbps
                      << " ul_pdcp_mbps=" << ulMbps << " dl_pdcp_bytes=" << cell.dl.rxPdcpSduBytes
                      << " ul_pdcp_bytes=" << cell.ul.rxPdcpSduBytes
                      << " dl_prb_grants=" << cell.dl.allocatedPrbGrantSum
                      << " ul_prb_grants=" << cell.ul.allocatedPrbGrantSum << std::endl;
            t.lastLiveLogNs=sample.endSimulationNs;
          }
        });
        provider->Start(); providers.push_back(std::move(provider));
        std::cout << "[HERO MEASURE] gnb_id=" << 101+i << " cell=" << nativeGnbs[i]->GetCellIds().front()
                  << " provider=ready interval_ms=" << sampleMs << std::endl;
      }
      if(enablePpo) {
        hero::learning::PpoActionClient::Config config;
        config.repositoryRoot=HERO_REPOSITORY_ROOT;
        config.tier=ppoTier; config.mode=ppoMode; config.formulation=ppoFormulation; config.checkpointDirectory=ppoCheckpointDir;
        config.experienceFile=ppoExperienceFile;
        config.maxGnbs=gnbCount; config.updateEvery=ppoUpdateEvery; config.seed=ppoSeed;
        config.maxEpisodes=ppoMaxEpisodes; config.episodeTransitions=ppoEpisodeTransitions;
        config.throughputWeight=ppoRewardThroughput; config.latencyWeight=ppoRewardLatency;
        config.reliabilityWeight=ppoRewardReliability; config.slaWeight=ppoRewardSla;
        config.controlCostWeight=ppoRewardControlCost;
        config.rewardQuality=ppoRewardQuality; config.rewardAdequacy=ppoRewardAdequacy;
        config.rewardUsefulEfficiency=ppoRewardUsefulEfficiency; config.rewardFairness=ppoRewardFairness;
        config.rewardActionStability=ppoRewardActionStability;
        // Start the Python child before FlexRIC or the dashboard reader starts
        // background threads; fork/exec after a multithreaded fork is unsafe.
        ppoClient=std::make_unique<hero::learning::PpoActionClient>(std::move(config));
        ppoClient->Start();
        std::cout<<"[HERO PPO] controller=ready tier="<<ppoTier<<" mode="<<ppoMode
                 <<" formulation="<<ppoFormulation<<" policy=Dirichlet-simplex updates_every="<<ppoUpdateEvery
                 <<" max_episodes="<<ppoMaxEpisodes<<" episode_transitions="<<ppoEpisodeTransitions
                 <<(ppoFormulation=="ue-aware" ? " reward=bounded-QAEFH weights=" : " reward=baseline-experimental throughput:");
        if(ppoFormulation=="ue-aware")
          std::cout<<"Q:"<<ppoRewardQuality<<",A:"<<ppoRewardAdequacy<<",E:"<<ppoRewardUsefulEfficiency
                   <<",F:"<<ppoRewardFairness<<",H:"<<ppoRewardActionStability;
        else
          std::cout<<ppoRewardThroughput<<",latency:"<<ppoRewardLatency<<",sla:"<<ppoRewardSla
                   <<",control_cost:"<<ppoRewardControlCost<<" reliability_weight=0";
        std::cout<<" checkpoint_dir="<<ppoCheckpointDir<<std::endl;
      }
      if(enableE2) {
        for(std::uint32_t i=0;i<gnbCount;i++) {
          auto port=static_cast<std::uint16_t>(ricPort);
          if(static_cast<int>(i)==failE2GnbIndex) port=static_cast<std::uint16_t>(ricPort+2);
          FlexricE2Agent::Endpoint endpoint{ricIp,plugins,port,5000};
          std::cout << "[HERO E2] gnb_id=" << 101+i << " connecting endpoint=" << ricIp << ':' << port
                    << " ran_functions=KPM-v3" << (enableControl ? ",HERO-RC-extension" : "") << std::endl;
          agents.push_back(std::make_unique<FlexricE2Agent>(nativeGnbs[i],std::move(endpoint),providers[i].get(),enableControl));
          E2NodeContext context{505,1,2,101+i,gnbs.Get(i)->GetId(),nativeGnbs[i]->GetCellIds()};
          agents.back()->Start(context);
        }
        for(std::uint32_t i=0;i<gnbCount;i++) {
          if(!agents[i]->WaitRegistered(5000)) {
            auto status=agents[i]->Status();
            std::cerr<<"[HERO E2] gnb_id="<<101+i<<" setup=failed state="<<status.state<<" detail="<<status.detail<<std::endl;
            if(!allowPartialE2) throw std::runtime_error("E2 setup failed for gNB "+std::to_string(101+i)+": "+status.detail);
          } else {
            std::cout << "[HERO E2] gnb_id=" << 101+i << " setup=success state=registered kpm_function_id=2"
                      << (enableControl ? " rc_function_id=3" : "") << std::endl;
          }
        }
      }
      reader=std::thread([&] { while(!readDone) { for(const auto& provider:providers) { (void)provider->LatestSnapshot(); (void)provider->LatestNativeSnapshot(); } std::this_thread::yield(); } });
    }
    helper->AttachToClosestGnb(ueDevices,gnbDevices);
    if(enableSliceEnforcement) {
      for(std::uint32_t i=0;i<gnbCount;i++) {
        const auto cell=nativeGnbs[i]->GetCellIds().front();
        const bool connected=nativeGnbs[i]->GetRrc()->TraceConnectWithoutContext("ConnectionEstablished",
          MakeBoundCallback(+[](std::shared_ptr<hero::slicing::SliceBudgetManager> manager,
                                const std::map<std::uint64_t,std::uint8_t>* sliceMap,
                                std::uint16_t targetCell, std::uint64_t imsi,
                                std::uint16_t cellId, std::uint16_t rnti) {
            if(cellId!=targetCell) return;
            const auto found=sliceMap->find(imsi);
            if(found==sliceMap->end() || !manager->RegisterUe(rnti,found->second)) {
              std::cerr<<"[HERO SLICE] rejected RRC mapping imsi="<<imsi<<" rnti="<<rnti<<std::endl;
              return;
            }
            std::cout<<"[HERO SLICE] gNB_cell="<<cellId<<" UE_imsi="<<imsi<<" rnti="<<rnti
                     <<" registered_slice="<<unsigned(found->second)<<std::endl;
          },sliceManagers[i],&cellSliceByImsi[i],cell));
        if(!connected) throw std::runtime_error("Could not connect native LENA ConnectionEstablished trace");
      }
    }
    if(sliceAssignment!="off") {
      for(std::uint32_t i=0;i<ueCount;i++) {
        const auto profile=ProfileFor(ueSlices[i],embbPacketBytes,embbRateBps,embbRequiredBps,
          urllcPacketBytes,urllcIntervalUs,urllcRequiredBps,urllcDelayTargetMs,urllcReliabilityTarget,
          mmtcPacketBytes,mmtcIntervalUs,mmtcRequiredBps,mmtcDelayTargetMs,mmtcReliabilityTarget);
        NrEpsBearer bearer(ueSlices[i]==1?NrEpsBearer::NGBR_VIDEO_TCP_DEFAULT:
                           ueSlices[i]==2?NrEpsBearer::DGBR_DISCRETE_AUT_SMALL:NrEpsBearer::NGBR_MC_DATA);
        auto tft=Create<NrEpcTft>();
        NrEpcTft::PacketFilter downlink; downlink.direction=NrEpcTft::DOWNLINK;
        downlink.localPortStart=9000+i; downlink.localPortEnd=9000+i; tft->Add(downlink);
        NrEpcTft::PacketFilter uplink; uplink.direction=NrEpcTft::UPLINK;
        uplink.remotePortStart=9100+i; uplink.remotePortEnd=9100+i; tft->Add(uplink);
        helper->ActivateDedicatedEpsBearer(ueDevices.Get(i),bearer,tft);
        std::cout<<"[HERO SLICE] imsi="<<DynamicCast<NrUeNetDevice>(ueDevices.Get(i))->GetImsi()
                 <<" slice_id="<<unsigned(profile.id)<<" slice="<<profile.name<<" app="<<profile.application
                 <<" bearer="<<profile.bearer<<" offered_bps="<<profile.offeredRateBps<<" required_dl_bps="
                 <<profile.requiredThroughputBps<<" delay_target_ms="<<profile.delayTargetMs
                 <<" reliability_target="<<profile.reliabilityTarget<<std::endl;
      }
      std::cout<<"[HERO SLICE] assignment="<<sliceAssignment<<" realized_embb="<<realized[1]
               <<" realized_urllc="<<realized[2]<<" realized_mmtc="<<realized[3]
               <<" seed="<<seed<<" enforcement="<<(enableSliceEnforcement?"enabled":"disabled (native scheduler baseline)")<<std::endl;
      if(enableSliceEnforcement) std::cout<<"[HERO SLICE BUDGET] unit=RBG-symbol-opportunities ratios="<<embbRatio<<','<<urllcRatio<<','<<mmtcRatio
        <<" policy=upper-cap leftover=unused runtime_updates="<<(enablePpo?"live-PPO-on-simulation-thread":"static configuration")<<std::endl;
    }
    std::cout << "[HERO RADIO] topology_ready gNBs=" << gnbCount << " UEs=" << ueCount
              << " gnb_spacing_m=" << gnbSpacingM << " ue_distance_m=" << ueDistanceM
              << " traffic=bidirectional-UDP" << std::endl;
    if (runUntilStopped) {
      std::signal(SIGINT, RequestStop);
      std::signal(SIGTERM, RequestStop);
      Simulator::Schedule(MilliSeconds(100), &PollStopSignal);
      std::cout << "[HERO] Continuous simulation active; radio-only mode runs unpaced and live UDP traffic alternates between 6 ms bursts and 24 ms intervals; stop it from the dashboard.\n" << std::flush;
    } else {
      Simulator::Stop(Seconds(duration));
    }
    Simulator::Run();
    ppoLive=false;
    for(auto& provider:providers) provider->SampleNow();
    readDone=true; if(reader.joinable()) reader.join();
    for(auto& agent:agents) agent->Stop();
    if(ppoClient) ppoClient->Stop();
    std::uint64_t deliveredDl=0,deliveredUl=0;
    for(std::uint32_t i=0;i<ueCount;i++) {
      auto ue=DynamicCast<NrUeNetDevice>(ueDevices.Get(i));
      std::cout<<"UE imsi="<<ue->GetImsi()<<" serving_cell="<<ue->GetRrc()->GetCellId()<<" rnti="<<ue->GetRrc()->GetRnti()<<" DL_UDP="<<dlServers[i]->GetReceived()<<" UL_UDP="<<ulServers[i]->GetReceived()<<'\n';
      deliveredDl+=dlServers[i]->GetReceived(); deliveredUl+=ulServers[i]->GetReceived();
    }
    for(std::uint32_t i=0;i<gnbCount;i++) {
      if(collect) {
        if(providers[i]->LatestSnapshot().sequence!=totals[i].windows || providers[i]->LatestNativeSnapshot().node.gnbId!=101+i)
          throw std::runtime_error("Per-gNB measurement publication/context mismatch");
        providers[i]->Stop();
      }
      std::cout<<"NATIVE_GNB gnb_id="<<101+i<<" cell="<<nativeGnbs[i]->GetCellIds().front()<<" windows="<<totals[i].windows
        <<" DL_SDU_bytes="<<totals[i].dlBytes<<" UL_SDU_bytes="<<totals[i].ulBytes
        <<" DL_PRB_grants="<<totals[i].dlPrbs<<" UL_PRB_grants="<<totals[i].ulPrbs<<'\n';
      if(enableSliceEnforcement) {
        const auto& finalRatios=sliceManagers[i]->GetRatios();
        std::cout<<"HERO_SLICE_POLICY gnb_id="<<101+i<<" policy_version="<<sliceManagers[i]->GetPolicyVersion()
          <<" source="<<sliceManagers[i]->GetPolicySource()<<" ratios="<<finalRatios[0]<<','<<finalRatios[1]<<','<<finalRatios[2]<<std::endl;
        const auto& dlUsed=sliceManagers[i]->GetCumulativeUsage(hero::slicing::Direction::Downlink);
        const auto& dlBudget=sliceManagers[i]->GetCumulativeBudgets(hero::slicing::Direction::Downlink);
        const auto& ulUsed=sliceManagers[i]->GetCumulativeUsage(hero::slicing::Direction::Uplink);
        const auto& ulBudget=sliceManagers[i]->GetCumulativeBudgets(hero::slicing::Direction::Uplink);
        std::cout<<"HERO_SLICE_ENFORCEMENT gnb_id="<<101+i<<" unit=RBG-symbol-opportunities"
          <<" DL_used="<<dlUsed[0]<<','<<dlUsed[1]<<','<<dlUsed[2]<<" DL_budget="<<dlBudget[0]<<','<<dlBudget[1]<<','<<dlBudget[2]
          <<" UL_used="<<ulUsed[0]<<','<<ulUsed[1]<<','<<ulUsed[2]<<" UL_budget="<<ulBudget[0]<<','<<ulBudget[1]<<','<<ulBudget[2]<<std::endl;
        for(std::size_t slice=0;slice<3;slice++) if(dlUsed[slice]>dlBudget[slice] || ulUsed[slice]>ulBudget[slice])
          throw std::runtime_error("Cumulative per-slice scheduler budget was exceeded");
      }
    }
    Simulator::Destroy();
    std::cout<<"NATIVE scenario_gnbs="<<gnbCount<<" UEs="<<ueCount<<" gnb_spacing_m="<<gnbSpacingM<<" ue_distance_m="<<ueDistanceM<<" DL_UDP="<<deliveredDl<<" UL_UDP="<<deliveredUl<<" collect="<<collect<<'\n';
    if(deliveredDl==0 || deliveredUl==0) return 1;
    if(sliceAssignment=="off" && !runUntilStopped && !enableE2 &&
       (deliveredDl!=ueCount*100 || deliveredUl!=ueCount*80)) return 1;
    for(const auto& t:totals) if(collect && (!t.dlBytes || !t.ulBytes || !t.dlPrbs || !t.ulPrbs)) return 1;
    std::cout<<"PASS native multi-gNB DL/UL measurement scenario\n";
    return 0;
  } catch(const std::exception& error) {
    readDone=true; if(reader.joinable()) reader.join();
    for(auto& agent:agents) agent->Stop();
    if(ppoClient) ppoClient->Stop();
    for(auto& provider:providers) provider->Stop();
    Simulator::Destroy(); std::cerr<<error.what()<<'\n'; return 1;
  }
}
