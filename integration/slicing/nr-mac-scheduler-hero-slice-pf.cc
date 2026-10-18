#include "nr-mac-scheduler-hero-slice-pf.h"

#include "ns3/nr-mac-scheduler-ue-info.h"

#include <ns3/log.h>
#include <ns3/uinteger.h>

#include <algorithm>
#include <array>
#include <limits>
#include <vector>

namespace ns3 {
NS_LOG_COMPONENT_DEFINE("NrMacSchedulerHeroSlicePF");
NS_OBJECT_ENSURE_REGISTERED(NrMacSchedulerHeroSlicePF);

TypeId NrMacSchedulerHeroSlicePF::GetTypeId() {
  static TypeId tid = TypeId("ns3::NrMacSchedulerHeroSlicePF")
      .SetParent<NrMacSchedulerOfdmaPF>()
      .AddConstructor<NrMacSchedulerHeroSlicePF>();
  return tid;
}

void NrMacSchedulerHeroSlicePF::SetSliceBudgetManager(
    std::shared_ptr<hero::slicing::SliceBudgetManager> manager) {
  m_sliceManager = std::move(manager);
}

NrMacSchedulerNs3::BeamSymbolMap NrMacSchedulerHeroSlicePF::AssignDLRBG(
    uint32_t symAvail, const ActiveUeMap& activeDl) const {
  auto result = NrMacSchedulerOfdmaPF::AssignDLRBG(symAvail, activeDl);
  Enforce(Direction::Downlink, symAvail, activeDl);
  return result;
}

NrMacSchedulerNs3::BeamSymbolMap NrMacSchedulerHeroSlicePF::AssignULRBG(
    uint32_t symAvail, const ActiveUeMap& activeUl) const {
  auto result = NrMacSchedulerOfdmaPF::AssignULRBG(symAvail, activeUl);
  Enforce(Direction::Uplink, symAvail, activeUl);
  return result;
}

void NrMacSchedulerHeroSlicePF::Enforce(Direction direction, uint32_t symAvail,
                                        const ActiveUeMap& active) const {
  if (!m_sliceManager) return;
  std::array<std::uint32_t, 3> budgets{};
  std::array<std::uint32_t, 3> used{};
  std::array<std::vector<std::shared_ptr<NrMacSchedulerUeInfo>>, 3> bySlice;

  for (std::uint8_t slice = 1; slice <= 3; ++slice) {
    budgets[slice - 1] = m_sliceManager->GetBudget(slice, GetBandwidthInRbg(), symAvail);
  }

  for (const auto& beam : active) {
    for (const auto& ue : beam.second) {
      std::uint8_t slice = 0;
      const bool known = m_sliceManager->GetSlice(ue.first->m_rnti, slice);
      if (!known) continue;  // Unknown RNTIs remain on the inherited scheduler policy.
      const auto rbgSymbols = direction == Direction::Downlink ? ue.first->m_dlRBG : ue.first->m_ulRBG;
      used[slice - 1] += rbgSymbols;
      bySlice[slice - 1].push_back(ue.first);
    }
  }

  const auto compare = direction == Direction::Downlink ? GetUeCompareDlFn() : GetUeCompareUlFn();
  for (std::size_t slice = 0; slice < 3; ++slice) {
    if (used[slice] <= budgets[slice] || bySlice[slice].empty()) continue;
    std::vector<UePtrAndBufferReq> ranked;
    ranked.reserve(bySlice[slice].size());
    for (const auto& ue : bySlice[slice]) ranked.emplace_back(ue, direction == Direction::Downlink ? ue->GetTotalDlBuffer() : 0U);
    std::stable_sort(ranked.begin(), ranked.end(), compare);  // Best PF priority first.
    std::uint32_t excess = used[slice] - budgets[slice];
    for (auto it = ranked.rbegin(); it != ranked.rend() && excess > 0; ++it) {
      auto& ue = it->first;
      auto& grant = direction == Direction::Downlink ? ue->m_dlRBG : ue->m_ulRBG;
      const auto symbols = direction == Direction::Downlink ? ue->m_dlSym : ue->m_ulSym;
      if (symbols == 0 || grant == 0) continue;
      // Grants are in whole RBG-symbol rows. Round the trim upward so the
      // resulting per-slice usage never exceeds the requested upper budget.
      const auto rowsToRemove = (excess + symbols - 1) / symbols;
      const auto actualDecrement = std::min<std::uint32_t>(grant, rowsToRemove * symbols);
      grant -= actualDecrement;
      excess = excess > actualDecrement ? excess - actualDecrement : 0;
      if (direction == Direction::Downlink) {
        ue->m_dlTbSize = grant ? m_dlAmc->CalculateTbSize(ue->m_dlMcs, ue->m_dlRank, grant * GetNumRbPerRbg()) : 0;
      } else {
        ue->m_ulTbSize = grant ? GetUlAmc()->CalculateTbSize(ue->m_ulMcs, ue->m_ulRank, grant * GetNumRbPerRbg()) : 0;
      }
      used[slice] = used[slice] > actualDecrement ? used[slice] - actualDecrement : 0;
    }
    NS_ASSERT_MSG(used[slice] <= budgets[slice], "HERO slice scheduler exceeded its per-slot RBG-symbol budget");
  }

  hero::slicing::SliceBudgetManager::Usage usedRecord{used[0], used[1], used[2]};
  hero::slicing::SliceBudgetManager::Usage budgetRecord{budgets[0], budgets[1], budgets[2]};
  m_sliceManager->Record(direction, usedRecord, budgetRecord);
}
}  // namespace ns3
