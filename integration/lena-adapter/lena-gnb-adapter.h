#pragma once
#include "common/interfaces.h"
#include "ns3/nr-gnb-net-device.h"
#include "ns3/nr-mac-scheduler.h"
namespace hero::oran {
// A read-only skeleton. Construct and use exclusively on the simulation thread.
class LenaGnbAdapter final {
 public:
  explicit LenaGnbAdapter(ns3::Ptr<ns3::NrGnbNetDevice> device);
  std::vector<std::uint16_t> CellIds() const;
  std::uint32_t BwpCount() const;
  ns3::Ptr<ns3::NrMacScheduler> Scheduler(std::uint8_t index) const;
 private:
  ns3::Ptr<ns3::NrGnbNetDevice> m_device;
};
}
