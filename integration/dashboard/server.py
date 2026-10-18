#!/usr/bin/env python3
"""Local configuration dashboard for the native HERO O-RAN integration."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import csv
import math
from collections import defaultdict, deque
from io import StringIO
import json
import os
from pathlib import Path
import re
import shlex
import signal
import shutil
import subprocess
import threading
import time
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
INTEGRATION = ROOT / "integration"
DASHBOARD = INTEGRATION / "dashboard"
BUILD = Path(os.environ.get("HERO_INTEGRATION_BUILD_DIR", INTEGRATION / "build")).resolve()
FLEXRIC_BUILD = Path(os.environ.get("FLEXRIC_BUILD_DIR", ROOT / "platforms/flexric/build")).resolve()
PROFILE_DIR = Path(os.environ.get("HERO_PROFILE_DIR", DASHBOARD / "profiles")).resolve()
PROFILE_DIR.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()

DEFAULT = {
    "name": "Single gNB demo",
    "gnbs": 1,
    "ues": 3,
    "gnbSpacingM": 500.0,
    "ueDistanceM": 20.0,
    "sliceAssignment": "off",
    "enableSliceEnforcement": False,
    "enablePpo": False,
    "ppoTier": "local",
    "ppoFormulation": "baseline",
    "ppoMode": "train",
    "ppoUpdateEvery": 8,
    "ppoSeed": 1,
    "ppoRewardThroughput": 1.0,
    "ppoRewardLatency": 0.0,
    "ppoRewardSla": 0.0,
    "ppoRewardControlCost": 0.0,
    "ppoRewardQuality": 0.50,
    "ppoRewardAdequacy": 0.20,
    "ppoRewardUsefulEfficiency": 0.15,
    "ppoRewardFairness": 0.10,
    "ppoRewardActionStability": 0.05,
    "embbRatio": 0.34,
    "urllcRatio": 0.33,
    "mmtcRatio": 0.33,
    "embbUes": 1,
    "urllcUes": 1,
    "mmtcUes": 1,
    "embbProbability": 0.4,
    "urllcProbability": 0.3,
    "mmtcProbability": 0.3,
    "seed": 1,
    "rngRun": 1,
    "embbPacketBytes": 1200,
    "embbRateBps": 10000000,
    "embbRequiredBps": 10000000,
    "urllcRequiredBps": 0,
    "urllcDelayTargetMs": 10.0,
    "urllcReliabilityTarget": 0.9999,
    "urllcPacketBytes": 128,
    "urllcIntervalUs": 10000,
    "mmtcPacketBytes": 64,
    "mmtcIntervalUs": 1000000,
    "mmtcRequiredBps": 0,
    "mmtcDelayTargetMs": 1000.0,
    "mmtcReliabilityTarget": 0.99,
    "sampleMs": 1000,
    "duration": 8,
    "runUntilStopped": True,
    "ricIp": "127.0.0.1",
    "connectRic": True,
    "kpmMonitor": True,
    "enableControl": True,
    "csv": "dashboard-measurements.csv",
}

# Derived only from fields written by MeasurementCsvWriter. These are local
# native telemetry choices; only the two throughput metrics are E2 KPM v3.
METRICS = {
    "dl_throughput": ("DL PDCP SDU throughput", "Mbit/s", "dl_delivered_sdu_throughput_mbps", "direct", ["cell", "ue"]),
    "ul_throughput": ("UL PDCP SDU throughput", "Mbit/s", "ul_delivered_sdu_throughput_mbps", "direct", ["cell", "ue"]),
    "dl_prb_grants": ("DL allocated PRB grant-sum", "PRB-grants/window", "dl_allocated_prb_grant_sum", "direct", ["cell", "ue"]),
    "ul_prb_grants": ("UL allocated PRB grant-sum", "PRB-grants/window", "ul_allocated_prb_grant_sum", "direct", ["cell", "ue"]),
    "active_ues": ("Active UEs", "UEs", "active_ues", "direct", ["cell"]),
    "connected_ues": ("Connected UEs at window end", "UEs", "connected_ues", "direct", ["cell"]),
    "dl_mcs": ("DL mean MCS", "index", "dl_mcs_sum,dl_mcs_samples", "mean", ["cell", "ue"]),
    "ul_mcs": ("UL mean MCS", "index", "ul_mcs_sum,ul_mcs_samples", "mean", ["cell", "ue"]),
    "dl_cqi": ("DL wideband mean CQI", "index", "dl_cqi_sum,dl_cqi_samples", "mean", ["cell"]),
    "dl_sinr": ("DL mean SINR", "dB", "dl_sinr_linear_sum,dl_sinr_samples", "sinr_db", ["cell", "ue"]),
    "ul_sinr": ("UL mean SINR", "dB", "ul_sinr_linear_sum,ul_sinr_samples", "sinr_db", ["cell", "ue"]),
    "dl_pdcp_delay": ("DL mean PDCP receive delay", "ms", "dl_pdcp_delay_mean_ns", "ns_ms", ["cell", "ue"]),
    "ul_pdcp_delay": ("UL mean PDCP receive delay", "ms", "ul_pdcp_delay_mean_ns", "ns_ms", ["cell", "ue"]),
    "dl_rlc_delay": ("DL mean RLC receive delay", "ms", "dl_rlc_delay_mean_ns", "ns_ms", ["cell", "ue"]),
    "ul_rlc_delay": ("UL mean RLC receive delay", "ms", "ul_rlc_delay_mean_ns", "ns_ms", ["cell", "ue"]),
    "dl_pdcp_packets": ("DL received PDCP packets", "PDUs/window", "dl_rx_pdcp_packets", "direct", ["cell", "ue"]),
    "ul_pdcp_packets": ("UL received PDCP packets", "PDUs/window", "ul_rx_pdcp_packets", "direct", ["cell", "ue"]),
}


def clean_config(raw: object) -> tuple[dict, list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["Configuration must be a JSON object."]
    cfg = dict(DEFAULT)
    allowed = set(DEFAULT)
    extra = set(raw) - allowed
    if extra:
        errors.append("Unsupported configuration fields: " + ", ".join(sorted(extra)))
    cfg.update({key: raw[key] for key in set(raw) & allowed})
    if "ppoFormulation" not in raw and cfg["ppoTier"] == "regional":
        cfg["ppoFormulation"] = "baseline"
    if not isinstance(cfg["name"], str) or not cfg["name"].strip() or len(cfg["name"]) > 80:
        errors.append("Profile name must contain 1..80 characters.")
    for key, low, high in (("gnbs", 1, 15), ("ues", 1, 32), ("sampleMs", 1, 10000)):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            errors.append(f"{key} must be an integer from {low} to {high}.")
    if isinstance(cfg["gnbs"], int) and isinstance(cfg["ues"], int) and cfg["ues"] < cfg["gnbs"]:
        errors.append("UE count must be at least the gNB count so every cell has a UE.")
    for key, low, high in (("gnbSpacingM", 50.0, 5000.0), ("ueDistanceM", 1.0, 500.0)):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            errors.append(f"{key} must be a finite number from {low:g} to {high:g} metres.")
        else:
            cfg[key] = float(value)
    if (isinstance(cfg["gnbs"], int) and cfg["gnbs"] > 1 and
            isinstance(cfg["gnbSpacingM"], (int, float)) and isinstance(cfg["ueDistanceM"], (int, float)) and
            cfg["ueDistanceM"] >= cfg["gnbSpacingM"] / 2):
        errors.append("UE distance from its assigned gNB must be less than half the inter-gNB spacing so nearest-cell attachment remains unambiguous.")
    if cfg["sliceAssignment"] not in ("off", "explicit", "random"):
        errors.append("sliceAssignment must be off, explicit, or random.")
    if not isinstance(cfg["enableSliceEnforcement"], bool):
        errors.append("enableSliceEnforcement must be true or false.")
    if not isinstance(cfg["enablePpo"], bool):
        errors.append("enablePpo must be true or false.")
    ratio_values = [cfg[k] for k in ("embbRatio", "urllcRatio", "mmtcRatio")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
           for v in ratio_values):
        errors.append("Slice budget ratios must each be finite and between 0 and 1.")
    elif not math.isclose(sum(ratio_values), 1.0, abs_tol=1e-9):
        errors.append("Slice budget ratios must sum to 1.")
    if cfg["enableSliceEnforcement"] and cfg["sliceAssignment"] == "off":
        errors.append("Slice budget enforcement requires explicit or seeded-random UE slice assignment.")
    if cfg["enablePpo"] and (not cfg["enableSliceEnforcement"] or cfg["sliceAssignment"] == "off"):
        errors.append("Live PPO requires enabled slice-budget enforcement and an explicit or seeded-random UE assignment.")
    if cfg["ppoTier"] not in ("local", "regional"):
        errors.append("ppoTier must be local or regional.")
    if cfg.get("ppoFormulation", "baseline") not in ("baseline", "ue-aware"):
        errors.append("ppoFormulation must be baseline or ue-aware.")
    if cfg["ppoTier"] == "regional" and cfg.get("ppoFormulation", "baseline") != "baseline":
        errors.append("UE-aware PPO is gNB-local only; choose the baseline formulation for regional mode.")
    if cfg["ppoMode"] not in ("train", "infer"):
        errors.append("ppoMode must be train or infer.")
    for key, low, high in (("ppoUpdateEvery", 1, 10000), ("ppoSeed", 1, 0xFFFFFFFF)):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            errors.append(f"{key} must be an integer from {low} to {high}.")
    for key in ("ppoRewardThroughput", "ppoRewardLatency", "ppoRewardSla", "ppoRewardControlCost"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            errors.append(f"{key} must be a finite nonnegative experimental reward weight.")
    ue_reward_keys = ("ppoRewardQuality", "ppoRewardAdequacy", "ppoRewardUsefulEfficiency",
                      "ppoRewardFairness", "ppoRewardActionStability")
    for key in ue_reward_keys:
        value = cfg.get(key, DEFAULT[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            errors.append(f"{key} must be a finite nonnegative UE-aware reward weight.")
    if all(isinstance(cfg.get(key, DEFAULT[key]), (int, float)) and not isinstance(cfg.get(key, DEFAULT[key]), bool)
           and math.isfinite(cfg.get(key, DEFAULT[key])) for key in ue_reward_keys):
        if not math.isclose(sum(cfg.get(key, DEFAULT[key]) for key in ue_reward_keys), 1.0, abs_tol=1e-9):
            errors.append("UE-aware reward weights Q/A/E/F/H must sum to 1.")
    if (cfg["enablePpo"] and cfg["ppoMode"] == "infer" and
            isinstance(cfg["gnbs"], int) and not isinstance(cfg["gnbs"], bool) and cfg["gnbs"] > 0):
        checkpoint_dir = BUILD / "ppo-checkpoints" / ("local" if cfg["ppoTier"] == "local" else f"regional-gnbs-{cfg['gnbs']}")
        prefix = "local-ue-aware" if cfg["ppoFormulation"] == "ue-aware" else "local"
        expected = ([checkpoint_dir / f"{prefix}-gnb-{101 + i}.pt" for i in range(cfg["gnbs"]) ]
                    if cfg["ppoTier"] == "local" else [checkpoint_dir / f"regional-gnbs-{cfg['gnbs']}.pt"])
        if not all(path.is_file() for path in expected):
            errors.append(f"PPO inference requires trained checkpoint(s) for this tier and gNB count under {checkpoint_dir}.")
    bounds = {"embbUes": (0, 32), "urllcUes": (0, 32), "mmtcUes": (0, 32),
              "seed": (1, 0xFFFFFFFF), "rngRun": (1, 0xFFFFFFFF),
              "embbPacketBytes": (1, 2000), "urllcPacketBytes": (1, 2000), "mmtcPacketBytes": (1, 2000),
              "embbRateBps": (1, 1_000_000_000), "embbRequiredBps": (0, 1_000_000_000),
              "urllcIntervalUs": (1, 10_000_000),
              "mmtcIntervalUs": (1, 10_000_000)}
    for key, (low, high) in bounds.items():
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            errors.append(f"{key} must be an integer from {low} to {high}.")
    for key in ("urllcRequiredBps", "mmtcRequiredBps"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
            errors.append(f"{key} must be an integer from 0 to 1000000000.")
    for key in ("urllcDelayTargetMs", "mmtcDelayTargetMs"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            errors.append(f"{key} must be a finite positive number of milliseconds.")
    for key in ("urllcReliabilityTarget", "mmtcReliabilityTarget"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            errors.append(f"{key} must be between 0 and 1.")
    for key in ("embbProbability", "urllcProbability", "mmtcProbability"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            errors.append(f"{key} must be between 0 and 1.")
    if cfg["sliceAssignment"] == "explicit" and all(isinstance(cfg[k], int) and not isinstance(cfg[k], bool) for k in ("embbUes", "urllcUes", "mmtcUes", "ues")) and cfg["embbUes"] + cfg["urllcUes"] + cfg["mmtcUes"] != cfg["ues"]:
        errors.append("Explicit eMBB, URLLC, and mMTC UE counts must sum to total UEs.")
    if cfg["sliceAssignment"] == "random" and all(isinstance(cfg[k], (int, float)) and not isinstance(cfg[k], bool) for k in ("embbProbability", "urllcProbability", "mmtcProbability")) and abs(cfg["embbProbability"] + cfg["urllcProbability"] + cfg["mmtcProbability"] - 1.0) > 1e-9:
        errors.append("Random slice probabilities must sum to 1.")
    duration = cfg["duration"]
    if not isinstance(cfg["runUntilStopped"], bool):
        errors.append("runUntilStopped must be true or false.")
    if isinstance(duration, bool) or not isinstance(duration, (float, int)) or not 1.5 <= duration <= 600:
        errors.append("Simulation duration must be between 1.5 and 600 seconds.")
    else:
        cfg["duration"] = float(duration)
    try:
        ipaddress.IPv4Address(cfg["ricIp"])
    except (ipaddress.AddressValueError, TypeError):
        errors.append("RIC address must be a valid IPv4 address.")
    for key in ("connectRic", "kpmMonitor", "enableControl"):
        if not isinstance(cfg[key], bool):
            errors.append(f"{key} must be true or false.")
    if isinstance(cfg["connectRic"], bool) and not cfg["connectRic"] and (cfg["kpmMonitor"] or cfg["enableControl"]):
        errors.append("KPM and control xApps require an E2 connection to the RIC.")
    large_topology = isinstance(cfg["gnbs"], int) and not isinstance(cfg["gnbs"], bool) and cfg["gnbs"] > 2
    if large_topology and all(isinstance(cfg[key], bool) for key in ("connectRic", "kpmMonitor", "enableControl")):
        # Large radio-only topologies are supported. The installed FlexRIC stack is
        # intentionally limited to the previously validated <=2-node E2 path.
        cfg["connectRic"] = False
        cfg["kpmMonitor"] = False
        cfg["enableControl"] = False
    if cfg["kpmMonitor"] and cfg["sampleMs"] != 1000:
        errors.append("The installed stock KPM xApp requests 1000 ms reports; set sample interval to 1000 ms while it is enabled.")
    if isinstance(cfg["gnbs"], int) and cfg["gnbs"] > 2 and cfg["kpmMonitor"]:
        errors.append("The installed stock FlexRIC KPM xApp is validated for at most two concurrent gNB subscriptions; disable it above two gNBs. The dashboard can still chart native LENA CSV measurements.")
    if not isinstance(cfg["csv"], str) or not cfg["csv"].strip() or Path(cfg["csv"]).name != cfg["csv"]:
        errors.append("CSV output must be a filename without directory components.")
    cfg["csv"] = str(cfg["csv"])
    return cfg, errors


def tail(path: Path, limit: int = 12000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            data = handle.read(limit)
        if size > limit:
            data = data.partition(b"\n")[2]
        return data.decode(errors="replace")
    except OSError:
        return ""


def sctp_listener_inode(port: int, table: str | None = None) -> str | None:
    """Return the kernel socket inode for an SCTP endpoint bound to this port."""
    if table is None:
        try:
            table = Path("/proc/net/sctp/eps").read_text()
        except OSError:
            return None
    lines = table.splitlines()
    if not lines:
        return None
    columns = lines[0].split()
    try:
        port_index, inode_index = columns.index("LPORT"), columns.index("INODE")
    except ValueError:
        return None
    for line in lines[1:]:
        fields = line.split()
        try:
            if int(fields[port_index]) == port:
                return fields[inode_index]
        except (ValueError, IndexError):
            continue
    return None


def sctp_socket_owner(inode: str) -> tuple[int, str] | None:
    """Resolve an SCTP socket inode to its owning PID and process command."""
    target = f"socket:[{inode}]"
    try:
        processes = os.scandir("/proc")
    except OSError:
        return None
    with processes:
        for process in processes:
            if not process.name.isdigit():
                continue
            try:
                fds = os.scandir(Path(process.path) / "fd")
            except OSError:
                continue
            found = False
            with fds:
                for fd in fds:
                    try:
                        if os.readlink(fd.path) == target:
                            found = True
                            break
                    except OSError:
                        continue
            if found:
                try:
                    command = Path(process.path, "cmdline").read_bytes().replace(b"\0", b" ").decode().strip()
                except OSError:
                    command = "unknown command"
                return int(process.name), command or "unknown command"
    return None


class Dashboard:
    def __init__(self) -> None:
        self.processes: dict[str, subprocess.Popen] = {}
        self.handles: dict[str, object] = {}
        self.log_paths: dict[str, Path] = {}
        self.requested: dict | None = None
        self.applied: dict | None = None
        self.state = "stopped"
        self.last_error = ""
        self.control_events: list[dict] = []
        self.external_services: dict[str, dict] = {}
        self.measurement_cache: dict[str, dict] = {}
        self.learning_cache: dict[str, dict] = {}

    def live(self, name: str) -> bool:
        proc = self.processes.get(name)
        return proc is not None and proc.poll() is None

    def status(self) -> dict:
        with LOCK:
            sim = self.processes.get("simulation")
            if self.state == "running" and sim is not None and sim.poll() is not None:
                if sim.returncode == 0:
                    self.state = "completed"
                else:
                    self.state = "error"
                    self.last_error = f"Native simulation exited with status {sim.returncode}; inspect its log."
            procs = {}
            for name, proc in self.processes.items():
                code = proc.poll()
                procs[name] = {"pid": proc.pid, "running": code is None, "exitCode": code,
                               "command": self.applied.get("commands", {}).get(name, "") if self.applied else ""}
            for name, service in self.external_services.items():
                procs[name] = {"pid": service.get("pid"), "running": True, "exitCode": None,
                               "command": service["command"], "managed": False}
            logs = {name: tail(path) for name, path in self.log_paths.items()}
            return {"state": self.state, "requested": self.requested, "applied": self.applied,
                    "processes": procs, "logs": logs, "error": self.last_error,
                    "controlEvents": self.control_events[-20:], "ricE2Port": 36421,
                    "ricXappPort": 36422, "runtimeModifiable": ["MaxDlMcs (private RC extension; 0..30)"]}

    def spawn(self, name: str, command: list[str], env: dict, suffix: str) -> subprocess.Popen:
        path = BUILD / "dashboard-runs" / suffix / f"{name}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("w")
        stdbuf = shutil.which("stdbuf")
        run_command = [stdbuf, "-oL", "-eL", *command] if stdbuf else command
        proc = subprocess.Popen(run_command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, start_new_session=True)
        def forward_output() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    handle.write(line)
                    handle.flush()
                    print(f"[{name}] {line}", end="", flush=True)
            finally:
                handle.close()
        threading.Thread(target=forward_output, name=f"dashboard-log-{name}", daemon=True).start()
        self.processes[name] = proc
        self.handles[name] = handle
        self.log_paths[name] = path
        return proc

    @staticmethod
    def wait_ric(proc: subprocess.Popen, bind_ip: str) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Near-RT RIC exited with status {proc.returncode}; inspect its log. Port 36421 may already be in use.")
            inode = sctp_listener_inode(36421)
            if inode is not None:
                owner = sctp_socket_owner(inode)
                if owner is None or owner[0] != proc.pid:
                    detail = f"PID {owner[0]} ({owner[1]})" if owner else "an unidentified process"
                    raise RuntimeError(f"SCTP port 36421 is already bound by {detail}; the newly launched Near-RT RIC does not own the listener. Stop the existing listener before launching this profile.")
                # Give startup assertions or delayed initialization a chance to surface.
                time.sleep(0.25)
                if proc.poll() is not None:
                    raise RuntimeError(f"Near-RT RIC exited during startup with status {proc.returncode}; inspect its log.")
                return
            time.sleep(0.1)
        raise RuntimeError(f"Near-RT RIC did not open SCTP port 36421 on {bind_ip} within 15 seconds.")

    def launch(self, raw: object) -> dict:
        cfg, errors = clean_config(raw)
        if errors:
            raise ValueError(" ".join(errors))
        with LOCK:
            if any(self.live(name) for name in ("ric", "kpm", "simulation")):
                raise RuntimeError("A configuration is already running; stop it before launching another.")
            self.requested = dict(raw) if isinstance(raw, dict) else cfg
            self.applied = None
            self.last_error = ""
            self.control_events = []
            self.external_services = {}
            self.measurement_cache = {}
            suffix = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
            env = os.environ.copy()
            env["HERO_INTEGRATION_BUILD_DIR"] = str(BUILD)
            env["FLEXRIC_BUILD_DIR"] = str(FLEXRIC_BUILD)
            env["HERO_FLEXRIC_PLUGIN_DIR"] = str(BUILD / "phase06-plugins")
            env["HERO_RIC_BIND_IP"] = cfg["ricIp"]
            commands: dict[str, list[str]] = {}
            try:
                if cfg["connectRic"]:
                    existing_inode = sctp_listener_inode(36421)
                    if existing_inode is not None:
                        owner = sctp_socket_owner(existing_inode)
                        if owner is None or not re.search(r"near-?rt-?ric", owner[1], re.IGNORECASE):
                            detail = f"PID {owner[0]} ({owner[1]})" if owner else f"socket inode {existing_inode} with unknown owner"
                            raise RuntimeError(f"SCTP port 36421 is occupied by {detail}, which could not be verified as the original FlexRIC Near-RT RIC. Free that port or start the compatible RIC before launching.")
                        description = f"PID {owner[0]} ({owner[1]})"
                        self.external_services["ric"] = {
                            "pid": owner[0] if owner else None,
                            "command": f"Reusing existing SCTP listener on {cfg['ricIp']}:36421 ({description}); not managed by dashboard",
                        }
                        ric_log = BUILD / "dashboard-runs" / suffix / "ric.log"
                        ric_log.parent.mkdir(parents=True, exist_ok=True)
                        ric_log.write_text(f"[dashboard] Reusing existing FlexRIC SCTP listener on {cfg['ricIp']}:36421 ({description}).\n")
                        self.log_paths["ric"] = ric_log
                    else:
                        ric_cmd = [str(INTEGRATION / "scripts/start-ric.sh")]
                        commands["ric"] = ric_cmd
                        ric = self.spawn("ric", ric_cmd, env, suffix)
                        self.wait_ric(ric, cfg["ricIp"])
                    if cfg["kpmMonitor"]:
                        kpm_cmd = [str(INTEGRATION / "scripts/start-kpm-monitor.sh"), "-a", cfg["ricIp"]]
                        commands["kpm"] = kpm_cmd
                        kpm = self.spawn("kpm", kpm_cmd, env, suffix)
                        time.sleep(0.5)
                        if kpm.poll() is not None:
                            raise RuntimeError(f"KPM xApp exited with status {kpm.returncode}; inspect its log.")
                ppo_checkpoint_dir = BUILD / "ppo-checkpoints" / (
                    "local" if cfg["ppoTier"] == "local" else f"regional-gnbs-{cfg['gnbs']}")
                native_cmd = [str(BUILD / "native-measurements"), f"--gnbs={cfg['gnbs']}",
                              f"--ues={cfg['ues']}", f"--gnbSpacingM={cfg['gnbSpacingM']:g}",
                              f"--ueDistanceM={cfg['ueDistanceM']:g}", f"--sampleMs={cfg['sampleMs']}",
                              f"--sliceAssignment={cfg['sliceAssignment']}", f"--embbUes={cfg['embbUes']}",
                              f"--enableSliceEnforcement={1 if cfg['enableSliceEnforcement'] else 0}",
                              f"--enablePpo={1 if cfg['enablePpo'] else 0}", f"--ppoTier={cfg['ppoTier']}",
                              f"--ppoFormulation={cfg.get('ppoFormulation', 'baseline')}",
                              f"--ppoMode={cfg['ppoMode']}", f"--ppoUpdateEvery={cfg['ppoUpdateEvery']}",
                              f"--ppoSeed={cfg['ppoSeed']}",
                              f"--ppoRewardThroughput={cfg['ppoRewardThroughput']:g}",
                              f"--ppoRewardLatency={cfg['ppoRewardLatency']:g}",
                              f"--ppoRewardSla={cfg['ppoRewardSla']:g}",
                              f"--ppoRewardControlCost={cfg['ppoRewardControlCost']:g}",
                              f"--ppoRewardQuality={cfg.get('ppoRewardQuality', 0.50):g}",
                              f"--ppoRewardAdequacy={cfg.get('ppoRewardAdequacy', 0.20):g}",
                              f"--ppoRewardUsefulEfficiency={cfg.get('ppoRewardUsefulEfficiency', 0.15):g}",
                              f"--ppoRewardFairness={cfg.get('ppoRewardFairness', 0.10):g}",
                              f"--ppoRewardActionStability={cfg.get('ppoRewardActionStability', 0.05):g}",
                              f"--ppoCheckpointDir={ppo_checkpoint_dir}",
                              f"--ppoExperienceFile={BUILD / 'dashboard-runs' / suffix / 'ppo-transitions.jsonl'}",
                              f"--embbRatio={cfg['embbRatio']:g}", f"--urllcRatio={cfg['urllcRatio']:g}",
                              f"--mmtcRatio={cfg['mmtcRatio']:g}",
                              f"--urllcUes={cfg['urllcUes']}", f"--mmtcUes={cfg['mmtcUes']}",
                              f"--embbProbability={cfg['embbProbability']:g}", f"--urllcProbability={cfg['urllcProbability']:g}",
                              f"--mmtcProbability={cfg['mmtcProbability']:g}", f"--seed={cfg['seed']}", f"--rngRun={cfg['rngRun']}",
                              f"--embbPacketBytes={cfg['embbPacketBytes']}", f"--embbRateBps={cfg['embbRateBps']}",
                              f"--embbRequiredBps={cfg['embbRequiredBps']}", f"--urllcPacketBytes={cfg['urllcPacketBytes']}",
                              f"--urllcIntervalUs={cfg['urllcIntervalUs']}", f"--urllcRequiredBps={cfg['urllcRequiredBps']}",
                              f"--urllcDelayTargetMs={cfg['urllcDelayTargetMs']:g}", f"--urllcReliabilityTarget={cfg['urllcReliabilityTarget']:g}",
                              f"--mmtcPacketBytes={cfg['mmtcPacketBytes']}", f"--mmtcIntervalUs={cfg['mmtcIntervalUs']}",
                              f"--mmtcRequiredBps={cfg['mmtcRequiredBps']}", f"--mmtcDelayTargetMs={cfg['mmtcDelayTargetMs']:g}",
                              f"--mmtcReliabilityTarget={cfg['mmtcReliabilityTarget']:g}",
                              f"--duration={0 if cfg['runUntilStopped'] else cfg['duration']:g}",
                              f"--csv={BUILD / 'dashboard-runs' / suffix / cfg['csv']}",
                              "--collect=1", f"--enableE2={1 if cfg['connectRic'] else 0}",
                              f"--enableControl={1 if cfg['enableControl'] else 0}",
                              f"--realtime={1 if cfg['connectRic'] else 0}",
                              f"--ricIp={cfg['ricIp']}", "--ricPort=36421",
                              f"--plugins={BUILD / ('phase06-plugins' if cfg['enableControl'] else 'phase02-plugins')}"]
                commands["simulation"] = native_cmd
                sim = self.spawn("simulation", native_cmd, env, suffix)
                self.applied = {"profile": cfg["name"], "values": cfg,
                                "commands": {key: shlex.join(cmd) for key, cmd in commands.items()},
                                "ricMode": ("reused-existing-listener" if "ric" in self.external_services else
                                            "started-by-dashboard" if cfg["connectRic"] else "disabled"),
                                "fixedPorts": {"E2 SCTP": 36421, "xApp SCTP": 36422},
                                "gnbIdentities": [101 + index for index in range(cfg["gnbs"])],
                                "simulationPacing": ("real-time paced (required for E2/SCTP)" if cfg["connectRic"]
                                                    else "unpaced (maximum available CPU speed)"),
                                "startupOnly": ["gNB/UE counts", "RIC address", "E2 connectivity", "xApp selection",
                                                "simulation duration", "measurement sampling interval"],
                                "appliedAt": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                                "runDirectory": str(BUILD / "dashboard-runs" / suffix)}
                self.state = "running" if sim.poll() is None else "completed"
                return self.status()
            except Exception as exc:
                self.last_error = str(exc)
                self.state = "error"
                self.stop()
                self.state = "error"
                raise

    def control(self, target: int, mcs: int) -> dict:
        with LOCK:
            if not self.applied or not self.applied["values"]["enableControl"]:
                raise RuntimeError("Private runtime control was not enabled in the applied startup profile.")
            if not self.live("simulation"):
                raise RuntimeError("Runtime control is only available while the native simulation is running.")
            identities = self.applied["gnbIdentities"]
            if target not in identities:
                raise ValueError(f"Target gNB must be one of the running identities: {identities}.")
            if isinstance(mcs, bool) or not isinstance(mcs, int) or not 0 <= mcs <= 30:
                raise ValueError("MaxDlMcs must be an integer from 0 to 30.")
            env = os.environ.copy()
            env.update({"HERO_INTEGRATION_BUILD_DIR": str(BUILD), "HERO_FLEXRIC_PLUGIN_DIR": str(BUILD / "phase06-plugins"),
                        "HERO_TARGET_GNB": str(target), "HERO_MAX_DL_MCS": str(mcs)})
            command = [str(BUILD / "xapp-hero-control"), "-p", str(BUILD / "phase06-plugins") + "/",
                       "-a", self.applied["values"]["ricIp"]]
            logdir = Path(self.applied["runDirectory"])
            handle = (logdir / "control-xapp.log").open("a")
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                code = proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=3)
                handle.close()
                raise RuntimeError("Control xApp timed out after 20 seconds; check FlexRIC/xApp logs.")
            handle.close()
            result = {"targetGnb": target, "requestedMaxDlMcs": mcs, "exitCode": code,
                      "log": tail(logdir / "control-xapp.log")}
            self.control_events.append(result)
            self.log_paths["control"] = logdir / "control-xapp.log"
            if code != 0 or "CONTROL_RESPONSE" not in result["log"]:
                raise RuntimeError(f"Control was rejected or not acknowledged (xApp exit {code}).")
            return result

    def stop(self) -> dict:
        with LOCK:
            for name in ("simulation", "kpm", "ric"):
                proc = self.processes.get(name)
                if proc is not None and proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGINT)
                        proc.wait(timeout=8)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                            proc.wait(timeout=4)
                        except (ProcessLookupError, subprocess.TimeoutExpired):
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                # The per-process output pump owns and closes its log handle after EOF.
                self.handles.pop(name, None)
            self.state = "stopped"
            return self.status()

    def measurements(self, metric_ids: list[str], scope: str, gnb_id: int | None) -> dict:
        with LOCK:
            applied = self.applied
            if not applied:
                return {"metrics": [], "series": [], "message": "Launch a scenario to collect native LENA measurements."}
            path = Path(applied["runDirectory"]) / applied["values"]["csv"]
            selected = [mid for mid in metric_ids if mid in METRICS]
            results = []
            if not path.is_file():
                return {"metrics": selected, "series": [], "message": "Measurement CSV is not available yet."}
            key = str(path)
            state = self.measurement_cache.get(key)
            if state is None or path.stat().st_size < state["offset"]:
                state = {"offset": 0, "pending": "", "header": None,
                         "points": defaultdict(lambda: deque(maxlen=180)), "sliceRows": {}}
                self.measurement_cache[key] = state
            with path.open("rb") as handle:
                handle.seek(state["offset"])
                appended = handle.read(512 * 1024)
            state["offset"] += len(appended)
            if appended:
                payload = state["pending"] + appended.decode("utf-8", errors="replace")
                lines = payload.splitlines(keepends=True)
                if lines and not lines[-1].endswith(("\n", "\r")):
                    state["pending"] = lines.pop()
                else:
                    state["pending"] = ""
                if state["header"] is None and lines:
                    state["header"] = lines.pop(0)
                if state["header"] is not None and lines:
                    for row in csv.DictReader(StringIO(state["header"] + "".join(lines))):
                        try:
                            node = int(row["gnb_id"])
                            t = int(row["end_sim_ns"]) / 1e9
                        except (ValueError, KeyError, TypeError):
                            continue
                        row_scope = row.get("scope")
                        entity = f"gNB {node}"
                        if row_scope == "ue":
                            entity += f" · UE {row.get('imsi') or '?'}"
                            try:
                                if int(row.get("slice_id") or 0):
                                    state.setdefault("sliceRows", {})[(node, row.get("imsi", ""))] = dict(row)
                            except ValueError:
                                pass
                        for mid, (_, _, columns, transform, scopes) in METRICS.items():
                            if row_scope not in scopes:
                                continue
                            values = [row.get(col, "") for col in columns.split(",")]
                            try:
                                if transform == "direct":
                                    value = float(values[0])
                                elif transform == "ns_ms":
                                    value = float(values[0]) / 1e6
                                elif transform == "mean":
                                    count = float(values[1])
                                    value = float(values[0]) / count if count > 0 else None
                                else:
                                    count = float(values[1])
                                    linear = float(values[0]) / count if count > 0 else 0
                                    value = 10 * math.log10(linear) if linear > 0 else None
                            except (ValueError, TypeError, IndexError):
                                value = None
                            if value is not None:
                                state["points"][(mid, row_scope, entity, node)].append((t, value))
            points = state["points"]
            for (mid, row_scope, entity, node), samples in points.items():
                if mid not in selected or row_scope != scope or (gnb_id is not None and node != gnb_id):
                    continue
                results.extend({"metric": mid, "series": entity, "gnbId": node, "t": t, "value": value}
                               for t, value in samples)
            return {"metrics": selected, "series": results, "message": "" if results else "No samples for this scope and selection yet."}

    def learning(self) -> dict:
        """Return actual PPO rewards and optimizer metrics appended by the live controller."""
        with LOCK:
            if not self.applied:
                return {"enabled": False, "rewards": [], "errors": [],
                        "components": [], "actions": [],
                        "message": "Launch a run with slice enforcement and PPO enabled."}
            values = self.applied.get("values", {})
            if not values.get("enablePpo"):
                return {"enabled": False, "rewards": [], "errors": [],
                        "components": [], "actions": [],
                        "message": "PPO learning is disabled for this run."}
            if values.get("ppoMode") != "train":
                return {"enabled": True, "tier": values.get("ppoTier"), "mode": "infer",
                        "rewards": [], "errors": [], "components": [], "actions": [],
                        "message": "Inference applies policy actions but does not calculate training rewards or PPO update losses."}
            run_dir = Path(self.applied["runDirectory"])
            path = run_dir / "ppo-transitions.jsonl"
            if not path.is_file():
                return {"enabled": True, "tier": values.get("ppoTier"), "mode": "train",
                        "rewards": [], "errors": [], "components": [], "actions": [],
                        "message": "Waiting for the first completed measurement window and PPO transition."}
            key = str(path)
            state = self.learning_cache.get(key)
            if state is None or path.stat().st_size < state["offset"]:
                state = {"offset": 0, "pending": "", "rewards": deque(maxlen=300),
                         "errors": deque(maxlen=300), "components": deque(maxlen=1200),
                         "actions": deque(maxlen=1200), "seen": 0}
                self.learning_cache[key] = state
            with path.open("rb") as stream:
                stream.seek(state["offset"])
                appended = stream.read(256 * 1024)
            state["offset"] += len(appended)
            if appended:
                payload = state["pending"] + appended.decode("utf-8", errors="replace")
                lines = payload.splitlines(keepends=True)
                if lines and not lines[-1].endswith(("\n", "\r")):
                    state["pending"] = lines.pop()
                else:
                    state["pending"] = ""
                for line in lines:
                    try:
                        record = json.loads(line)
                        tier = record.get("tier")
                        node_ids = [int(node) for node in record.get("gnb_ids", [])]
                        sim_time = float(record.get("simulation_time_ns", 0)) / 1e9
                        version = int(record.get("policy_version", 0))
                        if tier not in ("local", "regional") or not math.isfinite(sim_time):
                            continue
                        if record.get("record_type") == "transition" and isinstance(record.get("reward"), (int, float)):
                            reward = float(record["reward"])
                            if not math.isfinite(reward):
                                continue
                            if tier == "local":
                                for node in node_ids:
                                    state["rewards"].append({"series": f"gNB {node} · local PPO",
                                                              "gnbIds": [node], "t": sim_time,
                                                              "value": reward, "policyVersion": version})
                                    if record.get("formulation") == "ue-aware":
                                        for name, value in record.get("reward_terms", {}).items():
                                            if name in ("Q", "A", "E", "F", "H") and isinstance(value, (int, float)) and math.isfinite(float(value)):
                                                state["components"].append({"series": f"gNB {node} · {name}",
                                                    "metric": name, "gnbIds": [node], "t": sim_time,
                                                    "value": float(value), "policyVersion": version})
                                        bounds = record.get("reward_bounds") or {}
                                        for name, value in bounds.items():
                                            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                                                state["components"].append({"series": f"gNB {node} · {name}",
                                                    "metric": name, "gnbIds": [node], "t": sim_time,
                                                    "value": float(value), "policyVersion": version})
                                        diag = record.get("outcome_diagnostics") or {}
                                        ratios = diag.get("applied_ratios", [])
                                        slices = diag.get("slices", {})
                                        for index, name in enumerate(("embb", "urllc", "mmtc")):
                                            if index < len(ratios) and isinstance(ratios[index], (int, float)):
                                                state["actions"].append({"series": f"gNB {node} · {name} ratio",
                                                    "metric": "ratio", "gnbIds": [node], "t": sim_time,
                                                    "value": float(ratios[index]), "policyVersion": version})
                                            slice_data = slices.get(name, {}) if isinstance(slices, dict) else {}
                                            budget, used = slice_data.get("actual_budget_units"), slice_data.get("actual_mac_used_units")
                                            if isinstance(budget, (int, float)) and budget > 0 and isinstance(used, (int, float)):
                                                state["actions"].append({"series": f"gNB {node} · {name} budget use",
                                                    "metric": "budget_use", "gnbIds": [node], "t": sim_time,
                                                    "value": min(1.0, max(0.0, float(used) / float(budget))),
                                                    "policyVersion": version})
                            else:
                                label = "Near-RT RIC · regional PPO"
                                if node_ids:
                                    label += " · " + ", ".join(str(node) for node in node_ids)
                                state["rewards"].append({"series": label, "gnbIds": node_ids,
                                                          "t": sim_time, "value": reward,
                                                          "policyVersion": version})
                        elif record.get("record_type") == "update" and isinstance(record.get("metrics"), dict):
                            label = (f"gNB {node_ids[0]} · local PPO" if tier == "local" and node_ids
                                     else "Near-RT RIC · regional PPO")
                            for metric in ("actor_loss", "critic_loss", "entropy", "approx_kl", "gradient_norm"):
                                value = record["metrics"].get(metric)
                                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                                    state["errors"].append({"series": label, "metric": metric,
                                                            "gnbIds": node_ids, "t": sim_time,
                                                            "value": float(value),
                                                            "policyVersion": version})
                        state["seen"] += 1
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
            return {"enabled": True, "tier": values.get("ppoTier"), "mode": "train",
                    "rewards": list(state["rewards"]), "errors": list(state["errors"]),
                    "components": list(state["components"]), "actions": list(state["actions"]),
                    "records": state["seen"],
                    "message": "" if state["seen"] else "Waiting for the first completed measurement window and PPO transition."}

    def slices(self) -> dict:
        with LOCK:
            if not self.applied:
                return {"enabled": False, "counts": [], "ues": [], "message": "Launch a scenario with slice assignment enabled."}
            if self.applied.get("values", {}).get("sliceAssignment") == "off":
                return {"enabled": False, "counts": [], "ues": [], "message": "This run uses the baseline mode; enable explicit or random slice assignment to populate this view."}
            path = Path(self.applied["runDirectory"]) / self.applied["values"]["csv"]
            if path.is_file():
                self.measurements([], "ue", None)
            state = self.measurement_cache.get(str(path), {})
            grouped: dict[int, dict] = {}
            ues = []
            for row in state.get("sliceRows", {}).values():
                try:
                    sid, gnb = int(row.get("slice_id") or 0), int(row.get("gnb_id") or 0)
                    required = int(row.get("required_throughput_bps") or 0)
                    throughput = float(row.get("dl_delivered_sdu_throughput_mbps") or 0)
                except (ValueError, TypeError):
                    continue
                if not sid:
                    continue
                name = row.get("slice_type") or f"Slice {sid}"
                item = grouped.setdefault(sid, {"sliceId": sid, "name": name, "ues": 0, "gnbs": defaultdict(int),
                    "satisfied": 0, "violated": 0, "insufficient": 0})
                item["ues"] += 1
                item["gnbs"][gnb] += 1
                target = required / 1e6
                score = min(1.0, throughput / target) if target > 0 else None
                status = "SATISFIED" if score is not None and score >= 1 else "VIOLATED" if score is not None else "INSUFFICIENT_DATA"
                item[{"SATISFIED": "satisfied", "VIOLATED": "violated", "INSUFFICIENT_DATA": "insufficient"}[status]] += 1
                ues.append({"gnbId": gnb, "imsi": row.get("imsi"), "rnti": row.get("rnti"), "sliceId": sid,
                    "slice": name, "application": row.get("application_type"), "qosProfile": row.get("qos_profile"),
                    "throughputMbps": throughput, "requiredThroughputMbps": target, "throughputSatisfaction": score,
                    "delayTargetMs": float(row.get("delay_target_ms") or 0),
                    "reliabilityTarget": float(row.get("reliability_target") or 0), "status": status,
                    "allocatedDlPrbGrants": int(row.get("dl_allocated_prb_grant_sum") or 0),
                    "allocatedDlPrbSymbols": int(row.get("dl_allocated_prb_symbols") or 0),
                    "resourceEfficiencyBitsPerPrbSymbol": (float(row.get("dl_rx_pdcp_sdu_bytes") or 0) * 8 /
                        int(row.get("dl_allocated_prb_symbols") or 0)) if int(row.get("dl_allocated_prb_symbols") or 0) > 0 else None,
                    "reportedAtSimNs": int(row.get("end_sim_ns") or 0)})
            counts = []
            for item in grouped.values():
                item["gnbs"] = [{"gnbId": g, "ues": n} for g, n in sorted(item["gnbs"].items())]
                counts.append(item)
            return {"enabled": bool(counts), "counts": sorted(counts, key=lambda x: x["sliceId"]),
                    "ues": sorted(ues, key=lambda x: (x["gnbId"], x["sliceId"], int(x["imsi"] or 0))),
                    "message": "" if counts else "Waiting for the first native UE measurement window."}


MANAGER = Dashboard()


class Handler(BaseHTTPRequestHandler):
    server_version = "HERO-Configuration-Dashboard/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Polling generates frequent HTTP requests; keep the server terminal focused on
        # its running notice and the simulator/RIC/xApp output streamed by spawn().
        pass

    def send_json(self, data: object, status: int = 200) -> None:
        raw = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> object:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            raise ValueError("Request body is too large.")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            data = (DASHBOARD / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/status":
            self.send_json(MANAGER.status())
        elif parsed.path == "/api/measurements":
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            mids = query.get("metric", [])
            scope = query.get("scope", ["cell"])[0]
            raw_gnb = query.get("gnb", ["all"])[0]
            if scope not in ("cell", "ue"):
                self.send_json({"error": "Scope must be cell or ue."}, 400)
                return
            try:
                gnb_id = None if raw_gnb == "all" else int(raw_gnb)
            except ValueError:
                self.send_json({"error": "Invalid gNB identity."}, 400)
                return
            data = MANAGER.measurements(mids, scope, gnb_id)
            data["catalogue"] = [{"id": key, "name": value[0], "unit": value[1], "scopes": value[4],
                                  "wireKpm": key in ("dl_throughput", "ul_throughput")} for key, value in METRICS.items()]
            self.send_json(data)
        elif parsed.path == "/api/learning":
            self.send_json(MANAGER.learning())
        elif parsed.path == "/api/slices":
            self.send_json(MANAGER.slices())
        elif parsed.path == "/api/profiles":
            profiles = []
            for path in sorted(PROFILE_DIR.glob("*.json")):
                try:
                    profile = json.loads(path.read_text())
                    profiles.append({"file": path.name, "name": profile.get("name", path.stem), "config": profile})
                except (OSError, json.JSONDecodeError):
                    continue
            self.send_json({"profiles": profiles, "defaults": DEFAULT})
        elif parsed.path.startswith("/api/profile/"):
            filename = unquote(parsed.path.removeprefix("/api/profile/"))
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.json", filename):
                self.send_json({"error": "Invalid profile filename"}, 400)
                return
            target = (PROFILE_DIR / filename).resolve()
            if target.parent != PROFILE_DIR or not target.is_file():
                self.send_json({"error": "Profile not found"}, 404)
                return
            try:
                self.send_json(json.loads(target.read_text()))
            except json.JSONDecodeError:
                self.send_json({"error": "Profile JSON is invalid"}, 422)
        elif parsed.path.startswith("/api/log/"):
            name = unquote(parsed.path.removeprefix("/api/log/"))
            path = MANAGER.log_paths.get(name)
            self.send_json({"name": name, "text": tail(path) if path else ""})
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            data = self.body()
            path = urlparse(self.path).path
            if path == "/api/validate":
                cfg, errors = clean_config(data)
                notes = []
                if isinstance(data, dict) and isinstance(data.get("gnbs"), int) and data["gnbs"] > 2 and any(data.get(k) for k in ("connectRic", "kpmMonitor", "enableControl")):
                    notes.append("More than two gNBs use the verified native radio-only path. FlexRIC, the stock KPM xApp, and runtime control were turned off because the installed multi-node E2 path is validated only through two gNBs.")
                if isinstance(data, dict) and data.get("enablePpo") and data.get("connectRic"):
                    notes.append("Live PPO currently evaluates synchronously from the simulation event thread; policy inference and training can delay real-time E2 scheduling. Run radio-only PPO for uncoupled high-speed training.")
                self.send_json({"valid": not errors, "normalized": cfg, "errors": errors, "notes": notes}, 200 if not errors else 422)
            elif path == "/api/profiles/save":
                if not isinstance(data, dict) or not isinstance(data.get("config"), dict):
                    raise ValueError("Expected profile name and config object.")
                cfg, errors = clean_config(data["config"])
                if errors:
                    raise ValueError(" ".join(errors))
                name = str(data.get("name", cfg["name"])).strip()
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}", name):
                    raise ValueError("Profile filename may use letters, numbers, spaces, dot, underscore and dash.")
                cfg["name"] = name
                target = (PROFILE_DIR / (name.lower().replace(" ", "-") + ".json")).resolve()
                if target.parent != PROFILE_DIR:
                    raise ValueError("Invalid profile name.")
                target.write_text(json.dumps(cfg, indent=2) + "\n")
                self.send_json({"saved": target.name, "config": cfg})
            elif path == "/api/launch":
                self.send_json(MANAGER.launch(data), 202)
            elif path == "/api/control":
                if not isinstance(data, dict):
                    raise ValueError("Expected targetGnb and maxDlMcs.")
                self.send_json(MANAGER.control(data.get("targetGnb"), data.get("maxDlMcs")))
            elif path == "/api/stop":
                self.send_json(MANAGER.stop())
            else:
                self.send_json({"error": "Not found"}, 404)
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 422)
        except Exception as exc:  # surface launch/config errors to the operator
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def main() -> None:
    host = "127.0.0.1"  # Local process control has no authentication; never expose it on a network interface.
    port = int(os.environ.get("HERO_DASHBOARD_PORT", "8090"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"HERO System Configuration Manager: http://{host}:{port}")
    print(f"Integration build: {BUILD}; FlexRIC build: {FLEXRIC_BUILD}")
    print("Dashboard is running; HTTP access logs are suppressed. System process logs follow below.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        MANAGER.stop()
        server.server_close()


if __name__ == "__main__":
    main()
