"""Line-oriented PPO controller used by the native simulation-thread bridge.

The simulator sends completed, flushed CSV windows. Actions are returned only
after the policy has built observations from those real rows. Training rewards
are explicitly experimental and are computed only from available native data.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import Sequence

import torch

from .hero_ppo import (ActionRecord, ExperimentalRewardConfig, PpoConfig,
                       Transition, UeAwareRewardConfig, calculate_experimental_reward,
                       calculate_ue_aware_reward, normalized_jain_fairness, append_jsonl)
from .state import HeroStateBuilder, LocalObservation, RegionalObservation
from .tiers import LocalPpoAgent, RegionalPpoAgent

PROGRESS_INTERVAL_SECONDS = 5.0
_PROTOCOL_LOCK = threading.Lock()


def _protocol_write(stream, message: str) -> None:
    with _PROTOCOL_LOCK:
        stream.write(message + "\n")
        stream.flush()


def _diagnostic(stream, message: str) -> None:
    print(f"[HERO PPO IPC] {message}", file=stream, flush=True)


class CsvWindowReader:
    """Incrementally read appended MeasurementCsvWriter rows (avoid rereads)."""
    def __init__(self):
        self.path: Path | None = None
        self.stream = None
        self.fieldnames: list[str] = []
        self.offset = 0
        self.rows: dict[tuple[int, int], list[dict[str, str]]] = {}

    def read_new(self, path: Path) -> None:
        if self.path is None:
            self.path = path
            self.stream = path.open("r", encoding="utf-8", newline="")
            header = self.stream.readline()
            self.fieldnames = next(csv.reader([header]))
            self.offset = self.stream.tell()
        elif path.resolve() != self.path.resolve():
            raise ValueError("CSV path changed during a live PPO session")
        assert self.stream is not None
        self.stream.seek(self.offset)
        appended = self.stream.read()
        self.offset = self.stream.tell()
        for values in csv.reader(io.StringIO(appended)):
            if not values:
                continue
            if len(values) != len(self.fieldnames):
                raise ValueError("native measurement CSV row has an unexpected column count")
            row = dict(zip(self.fieldnames, values))
            key = (int(row["sequence"]), int(row["gnb_id"]))
            self.rows.setdefault(key, []).append(row)

    def window(self, sequence: int, node_id: int) -> list[dict[str, str]]:
        rows = self.rows.get((sequence, node_id), [])
        if not rows or not any(row.get("scope") == "cell" for row in rows):
            raise ValueError(f"no complete native measurement rows for sequence={sequence} gnb_id={node_id}")
        return rows

    def prune(self, before_sequence: int) -> None:
        self.rows = {key: rows for key, rows in self.rows.items() if key[0] >= before_sequence}


def _finite(row: dict[str, str], key: str) -> float | None:
    try:
        value = float(row.get(key, ""))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _outcome_terms(rows_by_gnb: Sequence[Sequence[dict[str, str]]],
                   control_cost: float) -> dict[str, float]:
    cells = [row for rows in rows_by_gnb for row in rows if row.get("scope") == "cell"]
    ue_rows = [row for rows in rows_by_gnb for row in rows if row.get("scope") == "ue"]
    if not cells:
        raise ValueError("reward needs at least one measured native cell row")
    rates = []
    delays = []
    for cell in cells:
        for key in ("dl_delivered_sdu_throughput_mbps", "ul_delivered_sdu_throughput_mbps"):
            value = _finite(cell, key)
            if value is not None:
                rates.append(min(max(value / 100.0, 0.0), 1.0))
        for key in ("dl_pdcp_delay_mean_ns", "ul_pdcp_delay_mean_ns"):
            value = _finite(cell, key)
            if value is not None and value >= 0:
                delays.append(min(max(1.0 - value / 1e9, 0.0), 1.0))
    sla = []
    for row in ue_rows:
        target = _finite(row, "required_throughput_bps")
        measured = _finite(row, "dl_delivered_sdu_throughput_mbps")
        if target is not None and target > 0 and measured is not None:
            sla.append(min(max(measured * 1e6 / target, 0.0), 1.0))
    return {
        "throughput": sum(rates) / len(rates) if rates else 0.0,
        "latency": sum(delays) / len(delays) if delays else 0.0,
        # Native packet-loss/reliability observations are not available yet.
        "reliability": 0.0,
        "sla": sum(sla) / len(sla) if sla else 0.0,
        "control_cost": min(max(control_cost, 0.0), 1.0),
    }


def _ue_aware_reward_terms(observation: LocalObservation,
                           rows: Sequence[dict[str, str]],
                           previous_applied_ratios: Sequence[float]) -> tuple[dict[str, float], dict[str, object]]:
    diagnostics = observation.diagnostics or {}
    slices = diagnostics.get("slices")
    applied = diagnostics.get("applied_ratios")
    if not isinstance(slices, dict) or not isinstance(applied, list) or len(applied) != 3 or any(v is None for v in applied):
        raise ValueError("UE-aware reward requires complete slice diagnostics and actual applied ratios")
    if len(previous_applied_ratios) != 3:
        raise ValueError("UE-aware reward is missing previous applied ratios")
    q_values: list[float] = []
    active_counts: dict[str, int] = {}
    demand_by_slice: dict[str, float | None] = {}
    qos_by_slice: dict[str, float | None] = {}
    for name in ("embb", "urllc", "mmtc"):
        record = slices[name]
        if not isinstance(record, dict):
            raise ValueError("malformed UE-aware slice diagnostic")
        active_counts[name] = int(record.get("active_ue_count", 0))
        satisfaction = record.get("ue_satisfaction", [])
        if not isinstance(satisfaction, list):
            raise ValueError("malformed UE satisfaction data")
        q_values.extend(float(value) for value in satisfaction)
        demand = record.get("estimated_demand_units")
        demand_by_slice[name] = float(demand) if demand is not None else None
        qos_by_slice[name] = float(record["qos_satisfaction"]) if record.get("qos_satisfaction") is not None else None
    if not q_values:
        raise ValueError("UE-aware QoS reward needs at least one connected UE with a measurable QoS target")
    quality = sum(q_values) / len(q_values)

    adequacy_values: list[tuple[int, float]] = []
    for name in ("embb", "urllc", "mmtc"):
        demand = demand_by_slice[name]
        record = slices[name]
        budget = record.get("actual_budget_units")
        if demand is None or budget is None:
            continue
        demand, budget = max(0.0, demand), max(0.0, float(budget))
        deficit = min(1.0, max(0.0, (demand - budget) / demand)) if demand > 0 else 0.0
        adequacy_values.append((active_counts[name], 1.0 - deficit))
    adequacy_weight = sum(weight for weight, _ in adequacy_values)
    adequacy = (sum(weight * value for weight, value in adequacy_values) / adequacy_weight
                if adequacy_weight else 0.0)

    capacity = diagnostics.get("capacity_units")
    useful_units = 0.0
    if capacity is not None and float(capacity) > 0:
        for name, slice_id in (("embb", 1), ("urllc", 2), ("mmtc", 3)):
            record = slices[name]
            demands = record.get("ue_demand_units", [])
            member_rows = [row for row in rows if row.get("scope") == "ue" and
                           int(_finite(row, "slice_id") or 0) == slice_id and
                           (_finite(row, "connected_ues") or 0) > 0]
            if len(demands) != len(member_rows):
                raise ValueError("UE demand records do not align with measured UE rows")
            for row, demand in zip(member_rows, demands):
                if demand is None:
                    continue
                prbs_per_rbg = _finite(row, "prbs_per_rbg") or 0.0
                prb_symbols = _finite(row, "dl_allocated_prb_symbols") or 0.0
                scheduled_bytes = _finite(row, "dl_mac_scheduled_tb_bytes") or 0.0
                delivered_bits = (_finite(row, "dl_rx_pdcp_sdu_bytes") or 0.0) * 8.0
                if prbs_per_rbg <= 0 or prb_symbols <= 0 or scheduled_bytes <= 0:
                    continue
                allocated_units = prb_symbols / prbs_per_rbg
                delivery_fraction = min(1.0, max(0.0, delivered_bits / (scheduled_bytes * 8.0)))
                useful_units += min(float(demand), allocated_units * delivery_fraction)
        efficiency = min(1.0, max(0.0, useful_units / float(capacity)))
    else:
        efficiency = 0.0

    fairness = normalized_jain_fairness(q_values)
    applied = [float(value) for value in applied]
    stability = min(1.0, max(0.0, 0.5 * sum(abs(a - b) for a, b in
                        zip(applied, previous_applied_ratios))))
    terms = {"Q": quality, "A": adequacy, "E": efficiency, "F": fairness, "H": stability}
    details = {"slice_qos_satisfaction": qos_by_slice,
               "slice_demand_units": demand_by_slice,
               "slice_active_ue_count": active_counts,
               "slice_adequacy_eligible": [name for name, demand in demand_by_slice.items()
                                             if demand is not None and slices[name].get("actual_budget_units") is not None],
               "estimated_useful_rbg_symbol_units": useful_units,
               "available_budget_rbg_symbol_units": float(capacity) if capacity is not None else None,
               "applied_ratios": applied,
               "previous_applied_ratios": list(previous_applied_ratios)}
    return terms, details


def _checkpoint_config(path: Path, seed: int) -> PpoConfig:
    if not path.exists():
        return PpoConfig(seed=seed)
    saved = torch.load(path, map_location="cpu", weights_only=False)
    values = dict(saved.get("config", {}))
    if "hidden_sizes" in values:
        values["hidden_sizes"] = tuple(values["hidden_sizes"])
    values["seed"] = seed
    return PpoConfig(**values)


class RuntimeController:
    def __init__(self, args):
        self.args = args
        self.formulation = getattr(args, "formulation", "baseline")
        self.experience_file = Path(args.experience_file) if args.experience_file else Path(args.checkpoint_dir) / "live-transitions.jsonl"
        self.builder = HeroStateBuilder()
        self.reader = CsvWindowReader()
        self.reward_config = ExperimentalRewardConfig(
            args.reward_throughput, args.reward_latency, args.reward_reliability,
            args.reward_sla, args.reward_control_cost, experimental=True)
        self.reward_config.validate()
        self.ue_reward_config = UeAwareRewardConfig(
            getattr(args, "reward_quality", 0.50), getattr(args, "reward_adequacy", 0.20),
            getattr(args, "reward_useful_efficiency", 0.15), getattr(args, "reward_fairness", 0.10),
            getattr(args, "reward_action_stability", 0.05))
        self.ue_reward_config.validate()
        if args.tier == "regional" and self.formulation != "baseline":
            raise ValueError("UE-aware formulation is currently local gNB-only; regional requires baseline")
        if self.formulation not in ("baseline", "ue-aware"):
            raise ValueError("formulation must be baseline or ue-aware")
        if args.reward_reliability != 0:
            raise ValueError("reliability reward weight must be 0 until native reliability telemetry exists")
        self.local_agents: dict[int, LocalPpoAgent] = {}
        self.regional: RegionalPpoAgent | None = None
        self.pending_local: dict[int, tuple[LocalObservation, ActionRecord, float, tuple[float, float, float]]] = {}
        self.pending_regional: tuple[RegionalObservation, ActionRecord, float, dict[int, tuple[float, float, float]]] | None = None
        self.previous_ratios: dict[int, tuple[float, float, float]] = {}
        self.steps = 0
        self.transitions = 0
        self._logged_local_schemas: set[int] = set()

    def _local_agent(self, node_id: int) -> LocalPpoAgent:
        if node_id not in self.local_agents:
            prefix = "local-ue-aware" if self.formulation == "ue-aware" else "local"
            path = Path(self.args.checkpoint_dir) / f"{prefix}-gnb-{node_id}.pt"
            config = _checkpoint_config(path, self.args.seed + node_id)
            agent = LocalPpoAgent(config, ue_aware=self.formulation == "ue-aware")
            if path.exists():
                agent.policy.load(path)
            elif self.args.mode == "infer":
                raise FileNotFoundError(f"local inference checkpoint not found: {path}")
            self.local_agents[node_id] = agent
        return self.local_agents[node_id]

    def _regional_agent(self) -> RegionalPpoAgent:
        if self.regional is None:
            path = Path(self.args.checkpoint_dir) / f"regional-gnbs-{self.args.max_gnbs}.pt"
            config = _checkpoint_config(path, self.args.seed)
            agent = RegionalPpoAgent(self.args.max_gnbs, config)
            if path.exists():
                agent.policy.load(path)
            elif self.args.mode == "infer":
                raise FileNotFoundError(f"regional inference checkpoint not found: {path}")
            self.regional = agent
        return self.regional

    def _reward_terms(self, rows: Sequence[Sequence[dict[str, str]]], cost: float) -> dict[str, float]:
        return _outcome_terms(rows, cost)

    def _reward(self, rows: Sequence[Sequence[dict[str, str]]], cost: float) -> float:
        return calculate_experimental_reward(self._reward_terms(rows, cost), self.reward_config)

    def _record_local_transition(self, node_id: int, observation: LocalObservation,
                                 rows: Sequence[dict[str, str]]) -> None:
        pending = self.pending_local.get(node_id)
        if pending is None or self.args.mode != "train":
            return
        old_observation, action, cost, _old_ratios = pending
        reward_details = None
        reward_diagnostics = None
        if self.formulation == "ue-aware":
            terms, reward_diagnostics = _ue_aware_reward_terms(observation, rows, _old_ratios)
            reward_details = calculate_ue_aware_reward(terms, self.ue_reward_config)
            reward = reward_details["reward"]
        else:
            terms = self._reward_terms([rows], cost)
            reward = calculate_experimental_reward(terms, self.reward_config)
        transition = Transition(old_observation.values, action.ratios,
                                action.log_probability, reward, observation.values,
                                False, False, observation.end_sim_ns,
                                action.policy_version, (node_id,))
        agent = self._local_agent(node_id)
        agent.trajectory.append(transition)
        transition_record = {
            "record_type": "transition",
            "tier": "local", "observation_sequence": old_observation.sequence,
            "outcome_sequence": observation.sequence, "simulation_time_ns": observation.end_sim_ns,
            "gnb_ids": [node_id], "policy_version": action.policy_version,
            "observation": old_observation.values, "action_ratios": action.ratios,
            "next_observation": observation.values, "reward_terms": terms,
            "reward": reward, "experimental_reward": True,
            "formulation": self.formulation,
            "outcome_diagnostics": ({
                "applied_ratios": (observation.diagnostics or {}).get("applied_ratios"),
                "capacity_units": (observation.diagnostics or {}).get("capacity_units"),
                "slices": {name: {key: (observation.diagnostics or {}).get("slices", {}).get(name, {}).get(key)
                                  for key in ("active_ue_count", "estimated_demand_units",
                                              "actual_budget_units", "actual_mac_used_units",
                                              "qos_satisfaction", "worst_ue_satisfaction",
                                              "satisfied_ue_fraction")}
                            for name in ("embb", "urllc", "mmtc")}
            } if self.formulation == "ue-aware" else None),
            "reward_diagnostics": reward_diagnostics,
            "reward_bounds": ({key: reward_details[key] for key in
                               ("global_lower_bound", "global_upper_bound",
                                "state_action_lower_bound", "state_action_upper_bound")}
                              if reward_details else None),
        }
        if (self.formulation == "ue-aware" and node_id not in self._logged_local_schemas
                and old_observation.diagnostics):
            transition_record["state_feature_names"] = old_observation.diagnostics.get("state_feature_names")
            self._logged_local_schemas.add(node_id)
        append_jsonl(self.experience_file, transition_record)
        self.transitions += 1
        if len(agent.trajectory) >= self.args.update_every:
            update_started = time.perf_counter()
            metrics = agent.policy.update(agent.trajectory.snapshot())
            update_duration_ms = (time.perf_counter() - update_started) * 1000.0
            agent.trajectory.clear()
            prefix = "local-ue-aware" if self.formulation == "ue-aware" else "local"
            path = Path(self.args.checkpoint_dir) / f"{prefix}-gnb-{node_id}.pt"
            agent.checkpoint(path)
            append_jsonl(self.experience_file, {
                "record_type": "update", "tier": "local",
                "simulation_time_ns": transition.simulation_time_ns,
                "gnb_ids": [node_id], "policy_version": agent.policy.policy_version,
                "metrics": metrics, "update_duration_ms": update_duration_ms,
            })
            print(f"[HERO PPO] tier=local gnb_id={node_id} update={agent.policy.policy_version} transitions={self.transitions} update_duration_ms={update_duration_ms:.3f} metrics={metrics}", file=sys.stderr, flush=True)
        self.pending_local.pop(node_id, None)

    def _record_regional_transition(self, observation: RegionalObservation,
                                    rows_by_gnb: Sequence[Sequence[dict[str, str]]],
                                    previous: dict[int, tuple[float, float, float]]) -> None:
        if self.pending_regional is None or self.args.mode != "train":
            return
        old_observation, action, cost, _old_ratios = self.pending_regional
        terms = self._reward_terms(rows_by_gnb, cost)
        reward = calculate_experimental_reward(terms, self.reward_config)
        transition = Transition(old_observation.values, action.ratios,
                                action.log_probability, reward, observation.values,
                                False, False, observation.end_sim_ns,
                                action.policy_version, observation.gnb_ids)
        agent = self._regional_agent()
        agent.trajectory.append(transition)
        append_jsonl(self.experience_file, {
            "record_type": "transition",
            "tier": "regional", "observation_sequence": old_observation.sequence,
            "outcome_sequence": observation.sequence, "simulation_time_ns": observation.end_sim_ns,
            "gnb_ids": observation.gnb_ids, "policy_version": action.policy_version,
            "observation": old_observation.values, "action_ratios": action.ratios,
            "next_observation": observation.values, "reward_terms": terms,
            "reward": reward, "experimental_reward": True,
        })
        self.transitions += 1
        if len(agent.trajectory) >= self.args.update_every:
            update_started = time.perf_counter()
            metrics = agent.policy.update(agent.trajectory.snapshot())
            update_duration_ms = (time.perf_counter() - update_started) * 1000.0
            agent.trajectory.clear()
            path = Path(self.args.checkpoint_dir) / f"regional-gnbs-{self.args.max_gnbs}.pt"
            agent.checkpoint(path)
            append_jsonl(self.experience_file, {
                "record_type": "update", "tier": "regional",
                "simulation_time_ns": transition.simulation_time_ns,
                "gnb_ids": list(observation.gnb_ids),
                "policy_version": agent.policy.policy_version, "metrics": metrics,
                "update_duration_ms": update_duration_ms,
            })
            print(f"[HERO PPO] tier=regional update={agent.policy.policy_version} transitions={self.transitions} update_duration_ms={update_duration_ms:.3f} metrics={metrics}", file=sys.stderr, flush=True)
        self.pending_regional = None

    def process(self, sequence: int, sim_end_ns: int, csv_path: Path,
                node_ids: list[int], initial: dict[int, tuple[float, float, float]]) -> dict[int, tuple[int, tuple[float, float, float]]]:
        self.reader.read_new(csv_path)
        rows_by_gnb = [self.reader.window(sequence, node_id) for node_id in node_ids]
        if self.args.tier == "local" and self.formulation == "ue-aware":
            observations = [self.builder.build_local_ue_aware(rows, self.previous_ratios.get(node_id, initial[node_id]))
                            for node_id, rows in zip(node_ids, rows_by_gnb)]
        else:
            observations = [self.builder.build_local(rows, self.previous_ratios.get(node_id, initial[node_id]))
                            for node_id, rows in zip(node_ids, rows_by_gnb)]
        result: dict[int, tuple[int, tuple[float, float, float]]] = {}

        if self.args.tier == "local":
            for node_id, observation, rows in zip(node_ids, observations, rows_by_gnb):
                self._record_local_transition(node_id, observation, rows)
                agent = self._local_agent(node_id)
                action = agent.act(observation, deterministic=self.args.mode == "infer")
                ratios = action.ratios[0]
                old = self.previous_ratios.get(node_id, initial[node_id])
                delta = sum(abs(a - b) for a, b in zip(ratios, old)) / 2.0
                self.pending_local[node_id] = (observation, action, delta, old)
                self.previous_ratios[node_id] = ratios
                result[node_id] = (action.policy_version, ratios)
        else:
            agent = self._regional_agent()
            observation = self.builder.build_regional(observations, self.args.max_gnbs)
            prior = dict(self.previous_ratios)
            self._record_regional_transition(observation, rows_by_gnb, prior)
            action = agent.act(observation, deterministic=self.args.mode == "infer")
            deltas = []
            for node_id in node_ids:
                ratios = action.ratios_by_gnb[node_id]
                old = self.previous_ratios.get(node_id, initial[node_id])
                deltas.append(sum(abs(a - b) for a, b in zip(ratios, old)) / 2.0)
                self.previous_ratios[node_id] = ratios
                result[node_id] = (action.sampled.policy_version, ratios)
            self.pending_regional = (observation, action.sampled,
                                     sum(deltas) / len(deltas), prior)

        self.steps += 1
        self.reader.prune(sequence - 1)
        return result


def serve_controller(controller, input_stream=None, output_stream=None,
                     diagnostic_stream=None, heartbeat_seconds: float | None = None) -> int:
    """Serve the simulator's line protocol and heartbeat while PPO computes an action."""
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    diagnostic_stream = diagnostic_stream or sys.stderr
    heartbeat_seconds = PROGRESS_INTERVAL_SECONDS if heartbeat_seconds is None else heartbeat_seconds
    try:
        _protocol_write(output_stream, "READY")
    except (BrokenPipeError, OSError) as error:
        _diagnostic(diagnostic_stream, f"event=stdout_closed phase=ready pid={os.getpid()} error={error}")
        return 1

    for line in input_stream:
        if line.strip() == "STOP":
            _diagnostic(diagnostic_stream, f"event=stop_received pid={os.getpid()}")
            break
        sequence = -1
        sim_end_ns = -1
        try:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 6 or fields[0] != "SAMPLE":
                raise ValueError("malformed simulator sample request")
            sequence, sim_end_ns = int(fields[1]), int(fields[2])
            csv_path = Path(fields[3])
            node_ids = [int(item) for item in fields[4].split(",") if item]
            initial = {}
            for record in fields[5].split(";"):
                if not record:
                    continue
                node, values = record.split(":", 1)
                ratios = tuple(float(v) for v in values.split(","))
                if len(ratios) != 3 or any(not math.isfinite(v) or v < 0 for v in ratios) or not math.isclose(sum(ratios), 1.0, abs_tol=1e-8):
                    raise ValueError(f"invalid initial ratio vector for gNB {node}")
                initial[int(node)] = ratios
            if sim_end_ns < 0 or not node_ids or set(node_ids) != set(initial):
                raise ValueError("sample needs matching positive gNB IDs and valid initial ratios")
        except Exception as error:
            try:
                _protocol_write(output_stream, f"ERROR\t{type(error).__name__}: {error}")
            except (BrokenPipeError, OSError) as pipe_error:
                _diagnostic(diagnostic_stream, f"event=stdout_closed phase=validation_error request_id={sequence} pid={os.getpid()} error={pipe_error}")
                return 1
            _diagnostic(diagnostic_stream, f"event=request_rejected request_id={sequence} error={type(error).__name__}:{error}")
            continue

        started = time.monotonic()
        heartbeat_stop = threading.Event()

        def heartbeat() -> None:
            while not heartbeat_stop.wait(heartbeat_seconds):
                elapsed_ms = int((time.monotonic() - started) * 1000)
                try:
                    _protocol_write(output_stream,
                                    f"BUSY\t{sequence}\tprocessing\t{elapsed_ms}")
                except (BrokenPipeError, OSError) as error:
                    _diagnostic(diagnostic_stream,
                                f"event=stdout_closed phase=processing request_id={sequence} sim_ns={sim_end_ns} pid={os.getpid()} elapsed_ms={elapsed_ms} error={error}")
                    return

        heartbeat_thread = threading.Thread(target=heartbeat,
                                            name=f"ppo-heartbeat-{sequence}", daemon=True)
        heartbeat_thread.start()
        actions = None
        failure = None
        try:
            actions = controller.process(sequence, sim_end_ns, csv_path, node_ids, initial)
        except Exception as error:
            failure = error
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(heartbeat_seconds + 1.0, 1.0))

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if failure is not None:
            response = f"ERROR\t{type(failure).__name__}: {failure}"
        else:
            payload = ";".join(f"{node}:{version}:{ratios[0]:.17g},{ratios[1]:.17g},{ratios[2]:.17g}"
                                for node, (version, ratios) in sorted(actions.items()))
            response = f"ACTION\t{payload}"
        try:
            _protocol_write(output_stream, response)
        except (BrokenPipeError, OSError) as error:
            _diagnostic(diagnostic_stream,
                        f"event=stdout_closed phase=response request_id={sequence} sim_ns={sim_end_ns} pid={os.getpid()} elapsed_ms={elapsed_ms} response={'ERROR' if failure else 'ACTION'} error={error}")
            return 1
        if failure is not None:
            _diagnostic(diagnostic_stream,
                        f"event=request_failed request_id={sequence} sim_ns={sim_end_ns} pid={os.getpid()} elapsed_ms={elapsed_ms} error={type(failure).__name__}:{failure}")
        else:
            _diagnostic(diagnostic_stream,
                        f"event=request_complete request_id={sequence} sim_ns={sim_end_ns} pid={os.getpid()} elapsed_ms={elapsed_ms} gnb_ids={','.join(map(str, node_ids))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=("local", "regional"), required=True)
    parser.add_argument("--mode", choices=("train", "infer"), required=True)
    parser.add_argument("--formulation", choices=("baseline", "ue-aware"), default="baseline")
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--experience-file", default="")
    parser.add_argument("--max-gnbs", type=int, required=True)
    parser.add_argument("--update-every", type=int, default=8)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--reward-throughput", type=float, default=1.0)
    parser.add_argument("--reward-latency", type=float, default=0.0)
    parser.add_argument("--reward-reliability", type=float, default=0.0)
    parser.add_argument("--reward-sla", type=float, default=0.0)
    parser.add_argument("--reward-control-cost", type=float, default=0.0)
    parser.add_argument("--reward-quality", type=float, default=0.50)
    parser.add_argument("--reward-adequacy", type=float, default=0.20)
    parser.add_argument("--reward-useful-efficiency", type=float, default=0.15)
    parser.add_argument("--reward-fairness", type=float, default=0.10)
    parser.add_argument("--reward-action-stability", type=float, default=0.05)
    args = parser.parse_args()
    if args.max_gnbs < 1 or args.update_every < 1:
        parser.error("max-gnbs and update-every must be positive")
    controller = RuntimeController(args)
    return serve_controller(controller)


if __name__ == "__main__":
    raise SystemExit(main())
