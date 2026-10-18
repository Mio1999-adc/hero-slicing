#include "e2-agent/flexric-e2-agent.h"
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/mobility-module.h"
#include "ns3/nr-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include <iostream>
#include <stdexcept>
using namespace ns3;
using namespace hero::oran;

int main(int argc,char **argv) {
  std::string ricIp="127.0.0.1",plugins=HERO_KPM_PLUGIN_DIR;
  std::uint16_t ricPort=36421,mcc=505,mnc=1;
  std::uint32_t mncDigits=2,gnbId=101,setupTimeoutMs=5000;
  double duration=2.0;
  bool enableE2=true,realtime=true;
  CommandLine cmd;
  cmd.AddValue("ricIp","IPv4 address of original FlexRIC RIC",ricIp);
  cmd.AddValue("ricPort","RIC SCTP port (stock Near-RT RIC uses 36421)",ricPort);
  cmd.AddValue("plugins","Directory containing only original libkpm_sm.so",plugins);
  cmd.AddValue("mcc","PLMN MCC",mcc); cmd.AddValue("mnc","PLMN MNC",mnc);
  cmd.AddValue("mncDigits","MNC digit length: 2 or 3",mncDigits);
  cmd.AddValue("gnbId","Explicit unique 32-bit E2 gNB ID",gnbId);
  cmd.AddValue("setupTimeoutMs","Bounded setup readiness wait",setupTimeoutMs);
  cmd.AddValue("duration","Simulation seconds, minimum 1",duration);
  cmd.AddValue("enableE2","Enable actual E2 connection; false is radio-only validation",enableE2);
  cmd.AddValue("realtime","Use ns-3 realtime simulator",realtime);
  cmd.Parse(argc,argv);
  if(duration<1 || mncDigits>3 || setupTimeoutMs==0) { std::cerr<<"Invalid duration/identity/timeout\n"; return 2; }
  if(realtime) GlobalValue::Bind("SimulatorImplementationType",StringValue("ns3::RealtimeSimulatorImpl"));
  RngSeedManager::SetSeed(1); RngSeedManager::SetRun(1);
  std::unique_ptr<FlexricE2Agent> agent;
  try {
    NodeContainer gnbs,ues,remote; gnbs.Create(1); ues.Create(1); remote.Create(1);
    MobilityHelper mobility; mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(gnbs); mobility.Install(ues);
    gnbs.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(0,0,10));
    ues.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(20,0,1.5));
    auto epc=CreateObject<NrPointToPointEpcHelper>();
    auto helper=CreateObject<NrHelper>(); helper->SetEpcHelper(epc);
    helper->SetBeamformingHelper(CreateObject<IdealBeamformingHelper>());
    helper->SetPathlossAttribute("ShadowingEnabled",BooleanValue(false));
    CcBwpCreator creator;
    CcBwpCreator::SimpleOperationBandConf conf(3.5e9,20e6,1,BandwidthPartInfo::UMa);
    auto band=creator.CreateOperationBandContiguousCc(conf);
    helper->InitializeOperationBand(&band);
    auto bwps=CcBwpCreator::GetAllBwps({band});
    auto gnbDevices=helper->InstallGnbDevice(gnbs,bwps);
    auto ueDevices=helper->InstallUeDevice(ues,bwps);
    helper->GetGnbPhy(gnbDevices.Get(0),0)->SetTxPower(30);
    helper->AssignStreams(gnbDevices,1); helper->AssignStreams(ueDevices,10);
    helper->UpdateDeviceConfigs(gnbDevices); helper->UpdateDeviceConfigs(ueDevices);
    InternetStackHelper internet; internet.Install(remote); internet.Install(ues);
    PointToPointHelper backhaul;
    backhaul.SetDeviceAttribute("DataRate",DataRateValue(DataRate("1Gb/s")));
    backhaul.SetDeviceAttribute("Mtu",UintegerValue(2500));
    backhaul.SetChannelAttribute("Delay",TimeValue(MilliSeconds(1)));
    auto link=backhaul.Install(epc->GetPgwNode(),remote.Get(0));
    Ipv4AddressHelper addresses; addresses.SetBase("1.0.0.0","255.0.0.0"); addresses.Assign(link);
    Ipv4StaticRoutingHelper routes;
    routes.GetStaticRouting(remote.Get(0)->GetObject<Ipv4>())->AddNetworkRouteTo(Ipv4Address("7.0.0.0"),Ipv4Mask("255.0.0.0"),1);
    auto ueAddresses=epc->AssignUeIpv4Address(ueDevices);
    routes.GetStaticRouting(ues.Get(0)->GetObject<Ipv4>())->SetDefaultRoute(epc->GetUeDefaultGatewayAddress(),1);
    helper->AttachToGnb(ueDevices.Get(0),gnbDevices.Get(0));
    UdpServerHelper server(9000); auto sink=server.Install(ues.Get(0));
    UdpClientHelper client(ueAddresses.GetAddress(0),9000);
    client.SetAttribute("MaxPackets",UintegerValue(100));
    client.SetAttribute("PacketSize",UintegerValue(512));
    client.SetAttribute("Interval",TimeValue(MilliSeconds(5)));
    auto source=client.Install(remote.Get(0));
    sink.Start(Seconds(0.1)); source.Start(Seconds(0.25));
    source.Stop(Seconds(duration-0.1)); sink.Stop(Seconds(duration));
    auto gnb=DynamicCast<NrGnbNetDevice>(gnbDevices.Get(0));
    auto ue=DynamicCast<NrUeNetDevice>(ueDevices.Get(0));
    std::cout<<"[NATIVE LENA] ns-3.42 / LENA v3.3.1 gNB node="<<gnbs.Get(0)->GetId()<<" cell="<<gnb->GetCellId()<<" BWPs="<<gnb->GetCcMapSize()<<" UE IMSI="<<ue->GetImsi()<<" E2 gNB_ID="<<gnbId<<std::endl;
    if(enableE2) {
      agent=std::make_unique<FlexricE2Agent>(gnb,FlexricE2Agent::Endpoint{ricIp,plugins,ricPort,setupTimeoutMs});
      E2NodeContext context{mcc,mnc,static_cast<std::uint8_t>(mncDigits),gnbId,gnbs.Get(0)->GetId(),gnb->GetCellIds()};
      agent->Start(context);
      if(!agent->WaitRegistered(setupTimeoutMs)) {
        auto state=agent->Status();
        throw std::runtime_error(std::string("E2 readiness failed/timed out: ")+state.detail);
      }
    }
    Simulator::Stop(Seconds(duration)); Simulator::Run();
    auto received=DynamicCast<UdpServer>(sink.Get(0))->GetReceived();
    bool radioOk=received>0;
    bool e2Ok=!agent || agent->Status().state==HERO_E2_REGISTERED;
    std::cout<<"[NATIVE LENA] simulated_seconds="<<Simulator::Now().GetSeconds()<<" UDP_DL_received="<<received<<" UE_RRC_state="<<ue->GetRrc()->GetState()<<" radio="<<(radioOk?"PASS":"FAIL")<<" E2="<<(agent?(e2Ok?"REGISTERED":"FAILED"):"DISABLED")<<std::endl;
    if(agent) agent->Stop();
    Simulator::Destroy();
    return radioOk && e2Ok ? 0 : 1;
  } catch(const std::exception& error) {
    std::cerr<<"[NATIVE LENA] ERROR: "<<error.what()<<std::endl;
    if(agent) agent->Stop();
    Simulator::Destroy(); return 1;
  }
}
