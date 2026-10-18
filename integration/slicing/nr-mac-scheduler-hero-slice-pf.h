#pragma once

#include "ns3/nr-mac-scheduler-ofdma-pf.h"
#include "ns3/nr-mac-scheduler-ns3.h"
#include "slice-budget-manager.h"

#include <memory>

namespace ns3 {

/** Opt-in PF OFDMA scheduler with upper RBG-symbol budgets per HERO slice.
 * The inherited PF allocator chooses UEs; this class trims over-budget grants.
 */
class NrMacSchedulerHeroSlicePF : public NrMacSchedulerOfdmaPF {
 public:
  static TypeId GetTypeId();
  NrMacSchedulerHeroSlicePF() = default;
  void SetSliceBudgetManager(std::shared_ptr<hero::slicing::SliceBudgetManager> manager);

 protected:
  BeamSymbolMap AssignDLRBG(uint32_t symAvail, const ActiveUeMap& activeDl) const override;
  BeamSymbolMap AssignULRBG(uint32_t symAvail, const ActiveUeMap& activeUl) const override;

 private:
  using Direction = hero::slicing::Direction;
  void Enforce(Direction direction, uint32_t symAvail, const ActiveUeMap& active) const;
  std::shared_ptr<hero::slicing::SliceBudgetManager> m_sliceManager;
};

}  // namespace ns3
