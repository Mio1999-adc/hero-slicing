#include "slice-budget-manager.h"

#include <cmath>
#include <stdexcept>
#include <utility>

namespace hero::slicing {

SliceBudgetManager::SliceBudgetManager(Ratios ratios) : m_ratios(ratios) {
  if (!ValidRatios(ratios)) throw std::invalid_argument("slice ratios must be finite, nonnegative, and sum to 1");
}

bool SliceBudgetManager::ValidRatios(const Ratios& ratios) {
  double sum = 0.0;
  for (double value : ratios) {
    if (!std::isfinite(value) || value < 0.0 || value > 1.0) return false;
    sum += value;
  }
  return std::abs(sum - 1.0) <= 1e-9;
}

bool SliceBudgetManager::SetRatios(Ratios ratios, std::uint64_t policyVersion, std::string source) {
  if (!ValidRatios(ratios) || source.empty()) return false;
  m_ratios = ratios;
  m_policyVersion = policyVersion;
  m_policySource = std::move(source);
  return true;
}

bool SliceBudgetManager::RegisterUe(std::uint16_t rnti, std::uint8_t sliceId) {
  if (rnti == 0 || sliceId < 1 || sliceId > 3) return false;
  m_ueSlices[rnti] = sliceId;
  return true;
}

void SliceBudgetManager::UnregisterUe(std::uint16_t rnti) { m_ueSlices.erase(rnti); }

bool SliceBudgetManager::GetSlice(std::uint16_t rnti, std::uint8_t& sliceId) const {
  const auto found = m_ueSlices.find(rnti);
  if (found == m_ueSlices.end()) return false;
  sliceId = found->second;
  return true;
}

std::uint32_t SliceBudgetManager::GetBudget(std::uint8_t sliceId, std::uint32_t nRbg,
                                           std::uint32_t availableSymbols) const {
  if (sliceId < 1 || sliceId > 3) return 0;
  const auto rbgPerSymbol = static_cast<std::uint32_t>(std::floor(m_ratios[sliceId - 1] * nRbg));
  return rbgPerSymbol * availableSymbols;
}

void SliceBudgetManager::Record(Direction direction, const Usage& used, const Usage& budgets) {
  const auto index = static_cast<std::size_t>(direction);
  m_lastUsage.at(index) = used;
  m_lastBudgets.at(index) = budgets;
  for (std::size_t slice = 0; slice < 3; ++slice) {
    m_cumulativeUsage.at(index)[slice] += used[slice];
    m_cumulativeBudgets.at(index)[slice] += budgets[slice];
    m_windowUsage.at(index)[slice] += used[slice];
    m_windowBudgets.at(index)[slice] += budgets[slice];
  }
}

const SliceBudgetManager::Usage& SliceBudgetManager::GetLastUsage(Direction direction) const {
  return m_lastUsage.at(static_cast<std::size_t>(direction));
}

const SliceBudgetManager::Usage& SliceBudgetManager::GetLastBudgets(Direction direction) const {
  return m_lastBudgets.at(static_cast<std::size_t>(direction));
}

const SliceBudgetManager::AggregateUsage& SliceBudgetManager::GetCumulativeUsage(Direction direction) const {
  return m_cumulativeUsage.at(static_cast<std::size_t>(direction));
}

const SliceBudgetManager::AggregateUsage& SliceBudgetManager::GetCumulativeBudgets(Direction direction) const {
  return m_cumulativeBudgets.at(static_cast<std::size_t>(direction));
}

SliceBudgetManager::WindowStats SliceBudgetManager::TakeWindowStats() {
  WindowStats stats{m_ratios, m_windowUsage, m_windowBudgets};
  m_windowUsage = {};
  m_windowBudgets = {};
  return stats;
}

}  // namespace hero::slicing
