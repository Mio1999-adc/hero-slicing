#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <map>
#include <signal.h>
#include <string>
#include <vector>

namespace hero::learning {

struct PpoAction {
  std::uint64_t policyVersion{};
  std::array<double, 3> ratios{};
};

/** Synchronous line-protocol client. Call only from the ns-3 simulation thread. */
class PpoActionClient {
 public:
  struct Config {
    std::string repositoryRoot;
    // Internal module override also supports protocol peer tests; production uses runtime_controller.
    std::string controllerModule{"integration.learning.runtime_controller"};
    std::string tier{"local"};
    std::string formulation{"ue-aware"};
    std::string mode{"train"};
    std::string checkpointDirectory;
    std::string experienceFile;
    std::uint32_t maxGnbs{1};
    std::uint32_t updateEvery{8};
    std::uint32_t maxEpisodes{100};
    std::uint32_t episodeTransitions{32};
    std::uint32_t seed{1};
    std::uint64_t responseIdleTimeoutMs{30'000};
    std::uint64_t maximumRequestMs{600'000};
    double throughputWeight{1.0};
    double latencyWeight{0.0};
    double reliabilityWeight{0.0};
    double slaWeight{0.0};
    double controlCostWeight{0.0};
    double rewardQuality{0.50};
    double rewardAdequacy{0.20};
    double rewardUsefulEfficiency{0.15};
    double rewardFairness{0.10};
    double rewardActionStability{0.05};
  };

  explicit PpoActionClient(Config config);
  ~PpoActionClient();
  PpoActionClient(const PpoActionClient&) = delete;
  PpoActionClient& operator=(const PpoActionClient&) = delete;

  void Start();
  std::map<std::uint32_t, PpoAction> Request(
      std::uint64_t sequence, std::uint64_t simEndNs, const std::string& csvPath,
      const std::vector<std::uint32_t>& gnbIds,
      const std::map<std::uint32_t, std::array<double, 3>>& currentRatios);
  void Stop() noexcept;

 private:
  std::string ReadLine(int timeoutMs);
  void WriteAll(const std::string& text);
  Config m_config;
  int m_input{-1};
  int m_output{-1};
  int m_child{-1};
  std::string m_readBuffer;
  struct sigaction m_previousSigpipe{};
  bool m_restoreSigpipe{false};
  std::size_t m_pendingRequests{0};
  std::uint64_t m_lastCompletedRequest{0};
  std::uint64_t m_completedRequests{0};
  std::uint64_t m_totalRequestLatencyUs{0};
  std::uint64_t m_maxRequestLatencyUs{0};
  std::uint64_t m_timeoutCount{0};
};

}  // namespace hero::learning
