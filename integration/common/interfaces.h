#pragma once
#include <cstdint>
#include <functional>
#include <optional>
#include <memory>
#include <string>
#include <vector>

namespace hero::oran {
// Proposed integration types, never wire representations or standard definitions.
struct E2NodeContext {
  std::uint16_t mcc{}, mnc{};
  std::uint8_t mncDigits{};
  std::uint32_t gnbId{}, simulationNodeId{};
  std::vector<std::uint16_t> cellIds;
};
struct UeContext {
  std::uint64_t imsi{};
  std::uint16_t cellId{}, rnti{};
  std::uint8_t bwpIndex{}, lcid{};
  // Local simulator metadata; not an E2SM or 5QI-defined slice identity.
  std::uint8_t sliceId{};
  std::string sliceType, applicationType, qosProfile;
  std::uint64_t offeredRateBps{}, requiredThroughputBps{};
  std::uint32_t packetSizeBytes{};
  std::uint64_t packetIntervalUs{};
  double delayTargetMs{}, reliabilityTarget{};
};
struct RanFunction { std::uint16_t id{}, revision{}; std::string oid; };
struct Measurement {
  std::string localName, unit;
  std::optional<UeContext> ue;
  std::optional<double> value; // missing is not zero
  std::uint16_t cellId{};
  std::uint8_t bwpIndex{};
};
struct MeasurementSnapshot {
  std::int64_t beginSimulationNs{}, endSimulationNs{};
  std::uint64_t sequence{};
  std::vector<Measurement> measurements;
  E2NodeContext node;
  std::int64_t collectStartTimeUs{}; // UTC epoch, for KPM indication header timestamp
};
struct SubscriptionKey {
  std::uint32_t requestorId{}, instanceId{};
  std::uint16_t ranFunctionId{};
  std::uint8_t actionId{};
};
struct KpmSubscription {
  SubscriptionKey key;
  std::uint32_t reportPeriodMs{};
  std::vector<std::string> measurements;
};
// Future C shim creates an owned, validated copy of decoded installed RC IEs.
// A concrete private payload hides C types and frees them using FlexRIC helpers.
class RcControlPayload {
 public:
  virtual ~RcControlPayload() = default;
};
struct RcControlRequest {
  std::uint64_t correlationId{};
  E2NodeContext node;
  std::shared_ptr<const RcControlPayload> payload;
};
enum class ControlStatus { Unsupported, Rejected, Queued, Applied, Failed };
struct ControlResult { ControlStatus status; std::string reason; };
class E2Agent {
 public:
  virtual ~E2Agent() = default;
  virtual void Start(const E2NodeContext&) = 0;
  virtual void Stop() = 0; // must quiesce callbacks and join before simulator destruction
};
class RanFunctionRegistry {
 public:
  virtual ~RanFunctionRegistry() = default;
  virtual std::vector<RanFunction> AdvertisedFunctions() const = 0;
};
class KpmMeasurementProvider {
 public:
  virtual ~KpmMeasurementProvider() = default;
  // Protocol threads read published snapshots only; never traverse ns-3 objects.
  virtual MeasurementSnapshot LatestSnapshot() const = 0;
};
class KpmSubscriptionManager {
 public:
  virtual ~KpmSubscriptionManager() = default;
  virtual bool Admit(const KpmSubscription&, std::string& reason) = 0;
  virtual void Remove(const SubscriptionKey&) = 0;
};
class KpmReporter {
 public:
  virtual ~KpmReporter() = default;
  virtual bool Report(const KpmSubscription&, const MeasurementSnapshot&) = 0;
};
class RcControlHandler {
 public:
  virtual ~RcControlHandler() = default;
  virtual ControlResult Submit(RcControlRequest) = 0;
};
class SimulationEventBridge {
 public:
  virtual ~SimulationEventBridge() = default;
  // Implementations own tasks, bound queue capacity, preserve FIFO within a node,
  // reject after Close, and invoke tasks only on the simulation thread.
  virtual bool Post(std::uint32_t simulationNodeId, std::function<void()> task) = 0;
  virtual void Close() = 0;
};
} // namespace hero::oran
