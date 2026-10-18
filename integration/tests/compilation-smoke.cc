#include "common/interfaces.h"
#include "lena-adapter/lena-gnb-adapter.h"
#include "ns3/nr-gnb-phy.h"
#include "ns3/nr-gnb-mac.h"
#include "ns3/nr-gnb-rrc.h"
#include "ns3/nr-ue-net-device.h"
#include "ns3/nr-pdcp.h"
#include "ns3/nr-rlc.h"
#include "ns3/nr-mac-scheduler-ns3.h"
#include "ns3/simulator.h"
#include "ns3/nr-epc-helper.h"
#include "ns3/nr-helper.h"
#include "ns3/cc-bwp-helper.h"
#include "ns3/mobility-helper.h"
#include "ns3/node-container.h"
#include <iostream>
#include <stdexcept>
#include <type_traits>
#include <thread>
extern "C" int hero_flexric_link_probe(void);
using namespace hero::oran;
static_assert(std::is_abstract_v<E2Agent> && std::is_abstract_v<RanFunctionRegistry>);
static_assert(std::is_abstract_v<KpmMeasurementProvider> && std::is_abstract_v<KpmSubscriptionManager>);
static_assert(std::is_abstract_v<KpmReporter> && std::is_abstract_v<RcControlHandler>);
static_assert(std::is_abstract_v<SimulationEventBridge>);
int main() {
  if (!hero_flexric_link_probe()) return 1;
  bool nullRejected = false;
  try { LenaGnbAdapter adapter(nullptr); }
  catch (const std::invalid_argument&) { nullRejected = true; }
  if (!nullRejected) return 2;
  ns3::NodeContainer nodes;
  nodes.Create(1);
  ns3::MobilityHelper mobility;
  mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  mobility.Install(nodes);
  auto helper = ns3::CreateObject<ns3::NrHelper>();
  ns3::CcBwpCreator creator;
  ns3::CcBwpCreator::SimpleOperationBandConf conf(3.5e9, 20e6, 1, ns3::BandwidthPartInfo::UMa);
  auto band = creator.CreateOperationBandContiguousCc(conf);
  helper->InitializeOperationBand(&band);
  auto bwps = ns3::CcBwpCreator::GetAllBwps({band});
  auto devices = helper->InstallGnbDevice(nodes, bwps);
  auto device = ns3::DynamicCast<ns3::NrGnbNetDevice>(devices.Get(0));
  device->UpdateConfig();
  LenaGnbAdapter adapter(device);
  if (adapter.BwpCount() != 1 || adapter.CellIds().size() != 1 || !adapter.Scheduler(0)) return 3;
  bool eventRan = false;
  // Initialize simulator on its owning thread before foreign-thread dispatch.
  (void)ns3::Simulator::Now();
  std::thread producer([&] {
    ns3::Simulator::ScheduleWithContext(7, ns3::NanoSeconds(1), [&] {
      eventRan = ns3::Simulator::GetContext() == 7;
    });
  });
  producer.join();
  ns3::Simulator::Stop(ns3::MicroSeconds(1));
  ns3::Simulator::Run();
  ns3::Simulator::Destroy();
  if (!eventRan) return 4;
  std::cout << "PASS: interface, LENA adapter, event API and FlexRIC link smoke (no E2 communication)\n";
}
