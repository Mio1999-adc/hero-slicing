#pragma once
#include "common/interfaces.h"
#include "e2-agent/flexric-agent.h"
#include "ns3/nr-gnb-net-device.h"
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
namespace hero::oran {
class LenaMeasurementProvider;
class FlexricE2Agent final : public E2Agent {
 public:
  struct Endpoint { std::string ip, pluginDirectory; std::uint16_t port=36421; std::uint32_t connectTimeoutMs=3000; };
  FlexricE2Agent(ns3::Ptr<ns3::NrGnbNetDevice>, Endpoint,
                 LenaMeasurementProvider* measurements=nullptr,
                 bool enablePrivateControl=false);
  ~FlexricE2Agent() override;
  void Start(const E2NodeContext&) override;
  void Stop() override;
  bool WaitRegistered(std::uint32_t timeoutMs);
  hero_e2_status Status() const;
  struct KpmReadContext {
    LenaMeasurementProvider* provider{};
    std::uint64_t lastSequence{};
  };
  struct ControlContext {
    ns3::NrGnbNetDevice* gnb{}; // valid until Stop() joins the protocol worker
    E2NodeContext node;
    std::mutex queueMutex;
    std::deque<std::function<void()>> pending;
    bool closed{true};
  };
  void PumpControlQueue();
 private:
  ns3::Ptr<ns3::NrGnbNetDevice> m_gnb;
  Endpoint m_endpoint;
  LenaMeasurementProvider* m_measurements{}; // outlives worker and agent shutdown
  bool m_enablePrivateControl{};
  KpmReadContext m_kpmReadContext; // per-E2-node reporting state
  ControlContext m_controlContext;
  hero_e2_agent *m_agent=nullptr;
};
}
