#include "ppo-action-client.h"

#include "slicing/slice-budget-manager.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

namespace hero::learning {
namespace {

using SteadyClock = std::chrono::steady_clock;

std::uint64_t WallClockMs() {
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::system_clock::now().time_since_epoch()).count());
}

std::uint64_t ElapsedMs(SteadyClock::time_point start) {
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
      SteadyClock::now() - start).count());
}

std::uint64_t ElapsedUs(SteadyClock::time_point start) {
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
      SteadyClock::now() - start).count());
}

void CloseFd(int& fd) noexcept {
  if (fd >= 0) {
    ::close(fd);
    fd = -1;
  }
}

std::vector<std::string> Split(const std::string& text, char separator) {
  std::vector<std::string> fields;
  std::stringstream stream(text);
  std::string field;
  while (std::getline(stream, field, separator)) fields.push_back(field);
  return fields;
}

std::uint64_t ParseUnsigned(const std::string& value) {
  std::size_t used = 0;
  const auto parsed = std::stoull(value, &used);
  if (used != value.size()) throw std::runtime_error("invalid unsigned integer in PPO response");
  return parsed;
}

double ParseDouble(const std::string& value) {
  std::size_t used = 0;
  const double parsed = std::stod(value, &used);
  if (used != value.size() || !std::isfinite(parsed))
    throw std::runtime_error("invalid finite number in PPO response");
  return parsed;
}

}  // namespace

PpoActionClient::PpoActionClient(Config config) : m_config(std::move(config)) {
  if (m_config.repositoryRoot.empty() || m_config.checkpointDirectory.empty() ||
      m_config.controllerModule.empty() ||
      (m_config.tier != "local" && m_config.tier != "regional") ||
      (m_config.formulation != "baseline" && m_config.formulation != "ue-aware") ||
      (m_config.tier == "regional" && m_config.formulation != "baseline") ||
      (m_config.mode != "train" && m_config.mode != "infer") ||
      m_config.maxGnbs == 0 || m_config.updateEvery == 0 ||
      m_config.responseIdleTimeoutMs == 0 || m_config.maximumRequestMs < m_config.responseIdleTimeoutMs)
    throw std::invalid_argument("invalid PPO controller configuration");
  struct sigaction ignore{};
  ignore.sa_handler = SIG_IGN;
  ::sigemptyset(&ignore.sa_mask);
  if (::sigaction(SIGPIPE, &ignore, &m_previousSigpipe) == 0) m_restoreSigpipe = true;
}

PpoActionClient::~PpoActionClient() {
  Stop();
  if (m_restoreSigpipe) (void)::sigaction(SIGPIPE, &m_previousSigpipe, nullptr);
}

void PpoActionClient::Start() {
  if (m_child > 0) return;
  int toChild[2]{-1, -1};
  int fromChild[2]{-1, -1};
  if (::pipe(toChild) != 0 || ::pipe(fromChild) != 0) {
    const auto message = std::string("cannot create PPO controller pipes: ") + std::strerror(errno);
    CloseFd(toChild[0]); CloseFd(toChild[1]); CloseFd(fromChild[0]); CloseFd(fromChild[1]);
    throw std::runtime_error(message);
  }
  const pid_t child = ::fork();
  if (child < 0) {
    const auto message = std::string("cannot fork PPO controller: ") + std::strerror(errno);
    CloseFd(toChild[0]); CloseFd(toChild[1]); CloseFd(fromChild[0]); CloseFd(fromChild[1]);
    throw std::runtime_error(message);
  }
  if (child == 0) {
    ::dup2(toChild[0], STDIN_FILENO);
    ::dup2(fromChild[1], STDOUT_FILENO);
    CloseFd(toChild[0]); CloseFd(toChild[1]); CloseFd(fromChild[0]); CloseFd(fromChild[1]);
    if (::chdir(m_config.repositoryRoot.c_str()) != 0) _exit(126);
    const std::string maxGnbs = std::to_string(m_config.maxGnbs);
    const std::string updateEvery = std::to_string(m_config.updateEvery);
    const std::string maxEpisodes = std::to_string(m_config.maxEpisodes);
    const std::string episodeTransitions = std::to_string(m_config.episodeTransitions);
    const std::string seed = std::to_string(m_config.seed);
    const std::string experienceFile = m_config.experienceFile;
    const std::string throughput = std::to_string(m_config.throughputWeight);
    const std::string latency = std::to_string(m_config.latencyWeight);
    const std::string reliability = std::to_string(m_config.reliabilityWeight);
    const std::string sla = std::to_string(m_config.slaWeight);
    const std::string controlCost = std::to_string(m_config.controlCostWeight);
    const std::string rewardQuality = std::to_string(m_config.rewardQuality);
    const std::string rewardAdequacy = std::to_string(m_config.rewardAdequacy);
    const std::string rewardUsefulEfficiency = std::to_string(m_config.rewardUsefulEfficiency);
    const std::string rewardFairness = std::to_string(m_config.rewardFairness);
    const std::string rewardActionStability = std::to_string(m_config.rewardActionStability);
    ::execlp("python3", "python3", "-u", "-m", m_config.controllerModule.c_str(),
             "--tier", m_config.tier.c_str(), "--mode", m_config.mode.c_str(),
             "--formulation", m_config.formulation.c_str(),
             "--checkpoint-dir", m_config.checkpointDirectory.c_str(),
             "--experience-file", experienceFile.c_str(),
             "--max-gnbs", maxGnbs.c_str(), "--update-every", updateEvery.c_str(),
             "--max-episodes", maxEpisodes.c_str(), "--episode-transitions", episodeTransitions.c_str(),
             "--seed", seed.c_str(),
             "--reward-throughput", throughput.c_str(), "--reward-latency", latency.c_str(),
             "--reward-reliability", reliability.c_str(), "--reward-sla", sla.c_str(),
             "--reward-control-cost", controlCost.c_str(),
             "--reward-quality", rewardQuality.c_str(), "--reward-adequacy", rewardAdequacy.c_str(),
             "--reward-useful-efficiency", rewardUsefulEfficiency.c_str(), "--reward-fairness", rewardFairness.c_str(),
             "--reward-action-stability", rewardActionStability.c_str(), static_cast<char*>(nullptr));
    _exit(127);
  }
  m_child = static_cast<int>(child);
  CloseFd(toChild[0]);
  CloseFd(fromChild[1]);
  m_input = toChild[1];
  m_output = fromChild[0];
  std::cerr << "[HERO PPO IPC] event=child_started child_pid=" << m_child
            << " stdin_fd=" << m_input << " stdout_fd=" << m_output << '\n';
  const auto ready = ReadLine(30000);
  if (ready != "READY") {
    Stop();
    throw std::runtime_error("PPO controller did not become ready: " + ready);
  }
}

void PpoActionClient::WriteAll(const std::string& text) {
  std::size_t written = 0;
  while (written < text.size()) {
    const auto count = ::write(m_input, text.data() + written, text.size() - written);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) {
      const int error = count == 0 ? EPIPE : errno;
      std::cerr << "[HERO PPO IPC] event=pipe_write_failed child_pid=" << m_child
                << " errno=" << error << " message=" << std::strerror(error) << '\n';
      throw std::runtime_error(std::string("PPO controller stdin write failed: ") + std::strerror(error) +
                               "; child_pid=" + std::to_string(m_child));
    }
    written += static_cast<std::size_t>(count);
  }
}

std::string PpoActionClient::ReadLine(int timeoutMs) {
  for (;;) {
    const auto newline = m_readBuffer.find('\n');
    if (newline != std::string::npos) {
      auto line = m_readBuffer.substr(0, newline);
      m_readBuffer.erase(0, newline + 1);
      if (!line.empty() && line.back() == '\r') line.pop_back();
      return line;
    }
    struct pollfd descriptor{m_output, POLLIN | POLLHUP, 0};
    int result;
    do { result = ::poll(&descriptor, 1, timeoutMs); } while (result < 0 && errno == EINTR);
    if (result == 0) throw std::runtime_error("timed out waiting for live PPO action");
    if (result < 0) throw std::runtime_error(std::string("PPO controller poll failed: ") + std::strerror(errno));
    char buffer[4096];
    const auto count = ::read(m_output, buffer, sizeof(buffer));
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) {
      if (m_child > 0) {
        const int child = m_child;
        const auto exitDeadline = SteadyClock::now() + std::chrono::milliseconds(100);
        do {
          int status = 0;
          const pid_t waited = ::waitpid(child, &status, WNOHANG);
          if (waited == child) {
            std::ostringstream detail;
            if (WIFEXITED(status)) detail << "exit_status=" << WEXITSTATUS(status);
            else if (WIFSIGNALED(status)) detail << "signal=" << WTERMSIG(status);
            else detail << "unknown_status=" << status;
            std::cerr << "[HERO PPO IPC] event=pipe_eof child_pid=" << child << ' ' << detail.str() << '\n';
            m_child = -1;
            throw std::runtime_error("PPO controller stdout reached EOF; child " + detail.str());
          }
          if (waited < 0 && errno == ECHILD) {
            m_child = -1;
            throw std::runtime_error("PPO controller stdout reached EOF; child was already reaped");
          }
          if (SteadyClock::now() >= exitDeadline) break;
          std::this_thread::sleep_for(std::chrono::milliseconds(5));
        } while (true);
        if (m_child > 0) {
          std::cerr << "[HERO PPO IPC] event=pipe_eof child_pid=" << child << " status=still-running\n";
          throw std::runtime_error("PPO controller stdout reached EOF while child_pid=" +
                                   std::to_string(child) + " remained running");
        }
      }
      throw std::runtime_error("PPO controller stdout reached EOF; child has already exited");
    }
    m_readBuffer.append(buffer, static_cast<std::size_t>(count));
  }
}

std::map<std::uint32_t, PpoAction> PpoActionClient::Request(
    std::uint64_t sequence, std::uint64_t simEndNs, const std::string& csvPath,
    const std::vector<std::uint32_t>& gnbIds,
    const std::map<std::uint32_t, std::array<double, 3>>& currentRatios) {
  if (m_child <= 0) throw std::logic_error("PPO controller is not started");
  if (gnbIds.empty() || gnbIds.size() > m_config.maxGnbs)
    throw std::invalid_argument("PPO request gNB count is empty or exceeds configured maximum");
  std::ostringstream request;
  request << std::setprecision(17);
  request << "SAMPLE\t" << sequence << '\t' << simEndNs << '\t' << csvPath << '\t';
  std::map<std::uint32_t, bool> expected;
  for (const auto id : gnbIds) {
    if (!expected.emplace(id, true).second || !currentRatios.contains(id))
      throw std::invalid_argument("PPO request gNB IDs must be unique and have current ratios");
  }
  for (std::size_t i = 0; i < gnbIds.size(); ++i) {
    if (i) request << ',';
    request << gnbIds[i];
  }
  request << '\t';
  for (const auto id : gnbIds) {
    const auto& ratios = currentRatios.at(id);
    if (!hero::slicing::SliceBudgetManager::ValidRatios(ratios))
      throw std::invalid_argument("invalid current slice ratio vector passed to PPO");
    request << id << ':' << ratios[0] << ',' << ratios[1] << ',' << ratios[2] << ';';
  }
  request << '\n';
  const auto started = SteadyClock::now();
  const auto wallStarted = WallClockMs();
  const auto idleTimeoutMs = m_config.responseIdleTimeoutMs;
  const auto maximumRequestMs = m_config.maximumRequestMs;
  ++m_pendingRequests;
  std::ostringstream idsText;
  for (std::size_t index = 0; index < gnbIds.size(); ++index) {
    if (index) idsText << ',';
    idsText << gnbIds[index];
  }
  std::cerr << "[HERO PPO IPC] event=request_begin request_id=" << sequence
            << " sim_ns=" << simEndNs << " wall_ms=" << wallStarted << " gnb_ids=" << idsText.str()
            << " child_pid=" << m_child << " pending=" << m_pendingRequests
            << " idle_timeout_ms=" << idleTimeoutMs << " hard_deadline_ms=" << maximumRequestMs << '\n';
  try {
    WriteAll(request.str());
    auto lastProgress = SteadyClock::now();
    std::string response;
    for (;;) {
      try {
        response = ReadLine(static_cast<int>(std::min<std::uint64_t>(idleTimeoutMs, INT32_MAX)));
      } catch (const std::runtime_error& error) {
        if (std::string(error.what()).find("timed out waiting for live PPO action") == std::string::npos)
          throw;
        const auto totalMs = ElapsedMs(started);
        const auto idleMs = ElapsedMs(lastProgress);
        if (totalMs >= maximumRequestMs || idleMs >= idleTimeoutMs) {
          ++m_timeoutCount;
          throw std::runtime_error("PPO request timed out request_id=" + std::to_string(sequence) +
              " sim_ns=" + std::to_string(simEndNs) + " child_pid=" + std::to_string(m_child) +
              " elapsed_ms=" + std::to_string(totalMs) + " idle_ms=" + std::to_string(idleMs) +
              " pending=" + std::to_string(m_pendingRequests) +
              " last_completed_request=" + std::to_string(m_lastCompletedRequest) +
              " timeout_count=" + std::to_string(m_timeoutCount));
        }
        continue;
      }
      if (response.rfind("BUSY\t", 0) == 0) {
        const auto fields = Split(response, '\t');
        if (fields.size() != 4 || ParseUnsigned(fields[1]) != sequence)
          throw std::runtime_error("PPO heartbeat has invalid or mismatched request ID");
        lastProgress = SteadyClock::now();
        const auto elapsed = ElapsedMs(started);
        if (elapsed >= maximumRequestMs) {
          ++m_timeoutCount;
          throw std::runtime_error("PPO hard deadline exceeded request_id=" + std::to_string(sequence) +
                                   " child_pid=" + std::to_string(m_child) +
                                   " elapsed_ms=" + std::to_string(elapsed));
        }
        continue;
      }
      if (ElapsedMs(started) >= maximumRequestMs) {
        ++m_timeoutCount;
        throw std::runtime_error("PPO hard deadline exceeded request_id=" + std::to_string(sequence) +
                                 " child_pid=" + std::to_string(m_child) +
                                 " elapsed_ms=" + std::to_string(ElapsedMs(started)));
      }
      break;
    }
    if (response.rfind("ERROR\t", 0) == 0)
      throw std::runtime_error("PPO controller rejected measurement window request_id=" +
                               std::to_string(sequence) + ": " + response.substr(6));
    if (response.rfind("ACTION\t", 0) != 0)
      throw std::runtime_error("malformed PPO controller response request_id=" + std::to_string(sequence));
    std::map<std::uint32_t, PpoAction> actions;
    const auto payload = response.substr(7);
    for (const auto& record : Split(payload, ';')) {
      if (record.empty()) continue;
      const auto fields = Split(record, ':');
      if (fields.size() != 3) throw std::runtime_error("malformed PPO action record");
      const auto id64 = ParseUnsigned(fields[0]);
      if (id64 > UINT32_MAX) throw std::runtime_error("PPO response gNB ID is out of range");
      const auto id = static_cast<std::uint32_t>(id64);
      const auto ratiosText = Split(fields[2], ',');
      if (ratiosText.size() != 3) throw std::runtime_error("PPO action must contain three slice ratios");
      PpoAction action{ParseUnsigned(fields[1]),
                       {ParseDouble(ratiosText[0]), ParseDouble(ratiosText[1]), ParseDouble(ratiosText[2])}};
      if (!expected.contains(id) || !hero::slicing::SliceBudgetManager::ValidRatios(action.ratios) ||
          !actions.emplace(id, action).second)
        throw std::runtime_error("PPO response has invalid, duplicate, or unexpected gNB action");
    }
    if (actions.size() != expected.size()) throw std::runtime_error("PPO response omitted one or more gNB actions");
    --m_pendingRequests;
    ++m_completedRequests;
    m_lastCompletedRequest = sequence;
    const auto latency = ElapsedUs(started);
    m_totalRequestLatencyUs += latency;
    m_maxRequestLatencyUs = std::max(m_maxRequestLatencyUs, latency);
    if (m_completedRequests == 1 || m_completedRequests % 100 == 0) {
      std::cerr << "[HERO PPO IPC] event=request_complete request_id=" << sequence
                << " sim_ns=" << simEndNs << " wall_ms=" << WallClockMs()
                << " elapsed_ms=" << static_cast<double>(latency) / 1000.0
                << " avg_ms=" << (static_cast<double>(m_totalRequestLatencyUs) / m_completedRequests / 1000.0)
                << " max_ms=" << (static_cast<double>(m_maxRequestLatencyUs) / 1000.0) << " child_pid=" << m_child
                << " pending=" << m_pendingRequests << " last_completed_request=" << m_lastCompletedRequest
                << " timeout_count=" << m_timeoutCount << '\n';
    }
    return actions;
  } catch (...) {
    --m_pendingRequests;
    std::cerr << "[HERO PPO IPC] event=request_failed request_id=" << sequence << " sim_ns=" << simEndNs
              << " wall_ms=" << WallClockMs() << " elapsed_ms=" << ElapsedMs(started)
              << " child_pid=" << m_child << " pending=" << m_pendingRequests
              << " last_completed_request=" << m_lastCompletedRequest
              << " timeout_count=" << m_timeoutCount << '\n';
    throw;
  }
}

void PpoActionClient::Stop() noexcept {
  const bool hadPipe = m_input >= 0 || m_output >= 0;
  const int childPidForLog = m_child;
  if (m_input >= 0) {
    const auto result = ::write(m_input, "STOP\n", 5);
    if (result < 0 && errno != EPIPE && errno != EBADF)
      std::cerr << "[HERO PPO IPC] event=stop_write_failed child_pid=" << m_child
                << " errno=" << errno << '\n';
    CloseFd(m_input);
  }
  if (m_child > 0) {
    auto waitForChild = [&](std::chrono::milliseconds timeout) {
      const auto deadline = SteadyClock::now() + timeout;
      while (SteadyClock::now() < deadline) {
        int status = 0;
        const pid_t result = ::waitpid(m_child, &status, WNOHANG);
        if (result == m_child) {
          std::cerr << "[HERO PPO IPC] event=child_exit child_pid=" << m_child;
          if (WIFEXITED(status)) std::cerr << " exit_status=" << WEXITSTATUS(status);
          else if (WIFSIGNALED(status)) std::cerr << " signal=" << WTERMSIG(status);
          std::cerr << '\n';
          m_child = -1;
          return true;
        }
        if (result < 0 && errno == ECHILD) { m_child = -1; return true; }
        if (result < 0 && errno != EINTR) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
      }
      return false;
    };
    if (!waitForChild(std::chrono::milliseconds(2000))) {
      std::cerr << "[HERO PPO IPC] event=child_shutdown_grace_expired child_pid=" << m_child
                << " pending=" << m_pendingRequests << "; sending SIGTERM\n";
      (void)::kill(m_child, SIGTERM);
      if (!waitForChild(std::chrono::milliseconds(2000))) {
        std::cerr << "[HERO PPO IPC] event=child_shutdown_term_expired child_pid=" << m_child
                  << "; sending SIGKILL\n";
        (void)::kill(m_child, SIGKILL);
        if (!waitForChild(std::chrono::milliseconds(1000)) && m_child > 0) {
          const pid_t deferredChild = m_child;
          std::cerr << "[HERO PPO IPC] event=child_reap_deferred child_pid=" << deferredChild << '\n';
          try {
            std::thread([deferredChild] {
              int status = 0;
              while (::waitpid(deferredChild, &status, 0) < 0 && errno == EINTR) {}
            }).detach();
          } catch (...) {
            // SIGKILL has been issued; if a reaper thread cannot be created,
            // the child will be reaped when the simulator process exits.
          }
          m_child = -1;
        }
      }
    }
  }
  CloseFd(m_output);
  if (hadPipe)
    std::cerr << "[HERO PPO IPC] event=pipes_closed child_pid=" << childPidForLog << '\n';
  if (m_child > 0) {
    m_child = -1;
  }
}

}  // namespace hero::learning
