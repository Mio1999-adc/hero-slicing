#pragma once
#include "common/interfaces.h"
#include <cstdint>
#include <optional>
#include <vector>
namespace hero::oran {
// Local measurement contracts, not E2SM wire types or standardized KPM names.
struct DirectionSample {
  std::uint64_t txPdcpPduBytes{}, txPdcpSduBytes{}, txPdcpPackets{};
  std::uint64_t rxPdcpPduBytes{}, rxPdcpSduBytes{}, rxPdcpPackets{};
  std::uint64_t txRlcPduBytes{}, txRlcPackets{}, rxRlcPduBytes{}, rxRlcPackets{};
  std::uint64_t pdcpRxDelayNsSum{}, rlcRxDelayNsSum{};
  std::uint64_t allocatedPrbGrantSum{}, allocatedPrbSymbols{}, dataGrants{};
  std::uint64_t macSchedulingEvents{}, macScheduledTbBytes{};
  std::uint64_t mcsSum{}, mcsSamples{};
  double sinrLinearSum{};
  std::uint64_t sinrSamples{};
};
struct NativeMeasurementRecord {
  std::uint16_t cellId{};
  std::uint8_t bwpIndex{};
  std::optional<UeContext> ue; // absent = gNB/cell total
  std::uint64_t contextGeneration{};
  bool connectedAtEnd{}, activeInWindow{}, gnbPdcpAttached{}, uePdcpAttached{}, ueReceiverAttached{};
  std::uint32_t availablePrbs{}, prbsPerRbg{}, symbolsPerSlot{}, connectedUes{}, activeUes{};
  std::uint64_t dlCqiSum{}, dlCqiSamples{};
  DirectionSample dl, ul;
};
struct NativeMeasurementSnapshot {
  E2NodeContext node;
  std::int64_t beginSimulationNs{}, endSimulationNs{};
  std::uint64_t sequence{}, unmappedEvents{};
  std::vector<NativeMeasurementRecord> records;
  std::int64_t collectStartTimeUs{};
};
}
