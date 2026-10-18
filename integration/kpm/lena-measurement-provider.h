#pragma once
#include "kpm/native-measurements.h"
#include "ns3/nr-gnb-net-device.h"
#include "ns3/nr-ue-net-device.h"
#include "ns3/nstime.h"
#include <functional>
#include <memory>
namespace hero::oran {
class LenaMeasurementProvider final : public KpmMeasurementProvider {
 public:
  using SampleObserver=std::function<void(const NativeMeasurementSnapshot&)>;
  LenaMeasurementProvider(ns3::Ptr<ns3::NrGnbNetDevice>, E2NodeContext, ns3::Time interval);
  ~LenaMeasurementProvider() override;
  LenaMeasurementProvider(const LenaMeasurementProvider&)=delete;
  LenaMeasurementProvider& operator=(const LenaMeasurementProvider&)=delete;
  // Lifecycle/registration/observer methods belong to the ns-3 owning thread.
  void RegisterUe(ns3::Ptr<ns3::NrUeNetDevice>, const UeContext& metadata = {});
  void SetObserver(SampleObserver);
  void Start();
  void Stop(); // cancels sample event, disconnects every trace; call before Destroy
  void SampleNow(); // flush partial interval; no row for zero-length window
  // These copies are safe for the FlexRIC reporter thread. No model traversal.
  NativeMeasurementSnapshot LatestNativeSnapshot() const;
  MeasurementSnapshot LatestSnapshot() const override;
 private:
  struct Impl;
  std::unique_ptr<Impl> m_impl;
};
}
