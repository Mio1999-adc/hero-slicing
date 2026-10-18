#include "slicing/slice-budget-manager.h"

#include <cassert>
#include <cmath>
#include <iostream>

int main() {
  using hero::slicing::Direction;
  using hero::slicing::SliceBudgetManager;
  SliceBudgetManager manager({0.5, 0.3, 0.2});
  assert(manager.GetBudget(1, 101, 2) == 100);
  assert(manager.GetBudget(2, 101, 2) == 60);
  assert(manager.GetBudget(3, 101, 2) == 40);
  assert(manager.RegisterUe(7, 2));
  std::uint8_t slice = 0;
  assert(manager.GetSlice(7, slice) && slice == 2);
  assert(!manager.RegisterUe(0, 1));
  assert(!manager.RegisterUe(8, 4));
  assert(!manager.SetRatios({0.5, 0.3, 0.3}));
  assert(manager.SetRatios({0.2, 0.6, 0.2}, 42, "local-ppo"));
  assert(manager.GetPolicyVersion() == 42 && manager.GetPolicySource() == "local-ppo");
  manager.Record(Direction::Downlink, {4, 5, 6}, {10, 20, 30});
  assert(manager.GetLastUsage(Direction::Downlink)[1] == 5);
  assert(manager.GetLastBudgets(Direction::Downlink)[2] == 30);
  manager.Record(Direction::Downlink, {1, 2, 3}, {5, 10, 15});
  assert(manager.GetCumulativeUsage(Direction::Downlink)[1] == 7);
  assert(manager.GetCumulativeBudgets(Direction::Downlink)[2] == 45);
  const auto window = manager.TakeWindowStats();
  assert(window.appliedRatios == SliceBudgetManager::Ratios({0.2, 0.6, 0.2}));
  assert(window.used[0][1] == 7 && window.budgets[0][2] == 45);
  const auto emptyWindow = manager.TakeWindowStats();
  assert(emptyWindow.used[0][1] == 0 && emptyWindow.budgets[0][2] == 0);
  manager.UnregisterUe(7);
  assert(!manager.GetSlice(7, slice));
  std::cout << "PASS slice budget manager validation, RBG-symbol floor budgets, UE isolation, policy update\n";
}
