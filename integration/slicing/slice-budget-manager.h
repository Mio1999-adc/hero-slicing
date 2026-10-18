#pragma once

#include <array>
#include <cstdint>
#include <map>
#include <string>

namespace hero::slicing {

enum class Direction : std::uint8_t { Downlink = 0, Uplink = 1 };

/** Per-gNB simulator-thread-only slice mapping and budget state.
 *  Budgets are fractions of available RBG-symbol opportunities in one slot;
 *  this is a scheduler budget, not a BWP bandwidth partition.
 */
class SliceBudgetManager {
 public:
  using Ratios = std::array<double, 3>;  // eMBB, URLLC, mMTC (slice IDs 1..3)
  using Usage = std::array<std::uint32_t, 3>;
  using AggregateUsage = std::array<std::uint64_t, 3>;
  struct WindowStats {
    Ratios appliedRatios{};
    std::array<AggregateUsage, 2> used{};
    std::array<AggregateUsage, 2> budgets{};
  };

  explicit SliceBudgetManager(Ratios ratios);

  static bool ValidRatios(const Ratios& ratios);
  bool SetRatios(Ratios ratios, std::uint64_t policyVersion = 0,
                 std::string source = "configuration");
  const Ratios& GetRatios() const { return m_ratios; }
  std::uint64_t GetPolicyVersion() const { return m_policyVersion; }
  const std::string& GetPolicySource() const { return m_policySource; }

  bool RegisterUe(std::uint16_t rnti, std::uint8_t sliceId);
  void UnregisterUe(std::uint16_t rnti);
  bool GetSlice(std::uint16_t rnti, std::uint8_t& sliceId) const;
  std::size_t GetUeCount() const { return m_ueSlices.size(); }

  std::uint32_t GetBudget(std::uint8_t sliceId, std::uint32_t nRbg,
                          std::uint32_t availableSymbols) const;
  void Record(Direction direction, const Usage& used, const Usage& budgets);
  const Usage& GetLastUsage(Direction direction) const;
  const Usage& GetLastBudgets(Direction direction) const;
  const AggregateUsage& GetCumulativeUsage(Direction direction) const;
  const AggregateUsage& GetCumulativeBudgets(Direction direction) const;
  // Snapshot and clear counters accumulated since the previous simulation
  // measurement boundary. Call on the ns-3 owning thread.
  WindowStats TakeWindowStats();

 private:
  Ratios m_ratios;
  std::uint64_t m_policyVersion{0};
  std::string m_policySource{"configuration"};
  std::map<std::uint16_t, std::uint8_t> m_ueSlices;
  std::array<Usage, 2> m_lastUsage{};
  std::array<Usage, 2> m_lastBudgets{};
  std::array<AggregateUsage, 2> m_cumulativeUsage{};
  std::array<AggregateUsage, 2> m_cumulativeBudgets{};
  std::array<AggregateUsage, 2> m_windowUsage{};
  std::array<AggregateUsage, 2> m_windowBudgets{};
};

}  // namespace hero::slicing
