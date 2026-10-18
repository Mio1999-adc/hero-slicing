#include "learning/ppo-action-client.h"

#include <dirent.h>
#include <sys/resource.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

std::size_t OpenFdCount() {
  std::size_t count = 0;
  DIR* directory = ::opendir("/proc/self/fd");
  if (!directory) return 0;
  while (const auto* entry = ::readdir(directory))
    if (entry->d_name[0] != '.') ++count;
  ::closedir(directory);
  return count;
}

void Require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

hero::learning::PpoActionClient::Config Config(const std::string& mode) {
  ::setenv("HERO_FAKE_PPO_MODE", mode.c_str(), 1);
  hero::learning::PpoActionClient::Config config;
  config.repositoryRoot = HERO_REPOSITORY_ROOT;
  config.controllerModule = "integration.tests.fake_ppo_controller";
  config.checkpointDirectory = "/tmp/hero-ppo-ipc-test-checkpoints";
  config.maxGnbs = 2;
  config.updateEvery = 8;
  return config;
}

void TestSlowMultiGnbResponse() {
  hero::learning::PpoActionClient client(Config("slow"));
  client.Start();
  const std::vector<std::uint32_t> ids{101, 102};
  const std::map<std::uint32_t, std::array<double, 3>> ratios{
      {101, {0.34, 0.33, 0.33}}, {102, {0.34, 0.33, 0.33}}};
  const auto actions = client.Request(47, 4'700'000'000ULL, "/tmp/unused.csv", ids, ratios);
  Require(actions.size() == 2 && actions.contains(101) && actions.contains(102),
          "slow multi-gNB response was lost or misrouted");
  Require(std::abs(actions.at(102).ratios[1] - 0.3) < 1e-12,
          "response ratio was incorrectly parsed");
  client.Stop();
}

void TestChildExitIsReported() {
  hero::learning::PpoActionClient client(Config("exit"));
  client.Start();
  bool reported = false;
  try {
    (void)client.Request(1, 1'000'000'000ULL, "/tmp/unused.csv", {101},
                         {{101, {0.34, 0.33, 0.33}}});
  } catch (const std::runtime_error& error) {
    reported = std::string(error.what()).find("exit_status=23") != std::string::npos;
  }
  Require(reported, "controller exit status was not reported to the simulator");
  client.Stop();
}

void TestInactiveChildTimesOutAndShutdownIsBounded() {
  auto config = Config("silent");
  config.responseIdleTimeoutMs = 100;
  config.maximumRequestMs = 1000;
  hero::learning::PpoActionClient client(std::move(config));
  client.Start();
  bool timedOut = false;
  try {
    (void)client.Request(8, 8'000'000'000ULL, "/tmp/unused.csv", {101},
                         {{101, {0.34, 0.33, 0.33}}});
  } catch (const std::runtime_error& error) {
    timedOut = std::string(error.what()).find("idle_ms=") != std::string::npos;
  }
  Require(timedOut, "silent child did not trigger an explicit bounded timeout");
  const auto started = std::chrono::steady_clock::now();
  client.Stop();
  const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
      std::chrono::steady_clock::now() - started).count();
  Require(elapsed < 5, "timed-out child shutdown blocked indefinitely");
}

void TestSixtyThousandRequestStress() {
  constexpr std::uint64_t requests = 60'010;
  const auto fdsBefore = OpenFdCount();
  hero::learning::PpoActionClient client(Config("fast"));
  client.Start();
  const std::vector<std::uint32_t> ids{101, 102};
  const std::map<std::uint32_t, std::array<double, 3>> ratios{
      {101, {0.34, 0.33, 0.33}}, {102, {0.34, 0.33, 0.33}}};
  std::uint64_t totalUs = 0, maxUs = 0;
  const auto overallStarted = std::chrono::steady_clock::now();
  for (std::uint64_t request = 1; request <= requests; ++request) {
    const auto started = std::chrono::steady_clock::now();
    const auto actions = client.Request(request, request * 1'000'000ULL,
                                        "/tmp/unused.csv", ids, ratios);
    const auto elapsed = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started).count());
    totalUs += elapsed;
    maxUs = std::max(maxUs, elapsed);
    Require(actions.size() == ids.size(), "stress response omitted a gNB action");
  }
  client.Stop();
  const auto fdsAfter = OpenFdCount();
  struct rusage usage{};
  (void)::getrusage(RUSAGE_SELF, &usage);
  const auto overallMs = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now() - overallStarted).count();
  Require(fdsAfter == fdsBefore, "PPO IPC stress test leaked file descriptors");
  std::cout << "PASS ppo-ipc-stress requests=" << requests
            << " avg_request_ms=" << static_cast<double>(totalUs) / requests / 1000.0
            << " max_request_ms=" << static_cast<double>(maxUs) / 1000.0 << " elapsed_ms=" << overallMs
            << " maxrss_kb=" << usage.ru_maxrss << " fd_before=" << fdsBefore
            << " fd_after=" << fdsAfter << '\n';
}

}  // namespace

int main() {
  try {
    TestSlowMultiGnbResponse();
    TestChildExitIsReported();
    TestInactiveChildTimesOutAndShutdownIsBounded();
    TestSixtyThousandRequestStress();
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL ppo-action-client-test: " << error.what() << '\n';
    return 1;
  }
}
