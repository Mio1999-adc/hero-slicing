#include "lena-adapter/lena-gnb-adapter.h"
#include <stdexcept>
namespace hero::oran {
LenaGnbAdapter::LenaGnbAdapter(ns3::Ptr<ns3::NrGnbNetDevice> device) : m_device(device) {
  if (!device) throw std::invalid_argument("LenaGnbAdapter requires a gNB device");
}
std::vector<std::uint16_t> LenaGnbAdapter::CellIds() const { return m_device->GetCellIds(); }
std::uint32_t LenaGnbAdapter::BwpCount() const { return m_device->GetCcMapSize(); }
ns3::Ptr<ns3::NrMacScheduler> LenaGnbAdapter::Scheduler(std::uint8_t index) const {
  // Upstream GetScheduler uses map::at; it rejects absent BWP keys.
  return m_device->GetScheduler(index);
}
}
