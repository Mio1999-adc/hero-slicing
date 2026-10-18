#pragma once
#include "kpm/native-measurements.h"
#include "slicing/slice-budget-manager.h"
#include <fstream>
#include <optional>
#include <string>
namespace hero::oran {
class MeasurementCsvWriter {
 public:
  explicit MeasurementCsvWriter(const std::string& path);
  void Write(const NativeMeasurementSnapshot&,
             const std::optional<hero::slicing::SliceBudgetManager::WindowStats>& = std::nullopt);
 private:
  std::ofstream m_file;
};
}
