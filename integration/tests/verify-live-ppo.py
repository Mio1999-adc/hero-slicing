#!/usr/bin/env python3
"""Run PPO against real native LENA samples and verify manager updates."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile


def run(binary: Path, output: Path, tier: str, formulation: str = "baseline") -> None:
    regional = tier == "regional"
    case_dir = Path(tempfile.mkdtemp(prefix=f"{tier}-", dir=output))
    checkpoint = case_dir / "checkpoints"
    csv_path = case_dir / "measurements.csv"
    command = [
        str(binary), "--gnbs=2" if regional else "--gnbs=1",
        "--ues=6" if regional else "--ues=3", "--duration=1.6", "--sampleMs=200",
        f"--csv={csv_path}", "--sliceAssignment=explicit",
        "--embbUes=2" if regional else "--embbUes=1",
        "--urllcUes=2" if regional else "--urllcUes=1",
        "--mmtcUes=2" if regional else "--mmtcUes=1",
        "--embbRateBps=1000000", "--enableSliceEnforcement=1", "--enablePpo=1",
        f"--ppoTier={tier}", "--ppoMode=train", "--ppoUpdateEvery=2", "--ppoMaxEpisodes=2",
        "--ppoEpisodeTransitions=2", "--ppoSeed=41",
        f"--ppoFormulation={formulation}",
        f"--ppoCheckpointDir={checkpoint}", "--enableE2=0", "--realtime=0",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=90)
    output_text = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise AssertionError(f"{tier} PPO scenario failed ({completed.returncode}):\n{output_text}")
    expected_nodes = (101, 102) if regional else (101,)
    for node in expected_nodes:
        applied_lines = [line for line in output_text.splitlines()
                         if f"[HERO PPO] applied tier={tier}" in line and f"gnb_id={node} " in line]
        if not applied_lines:
            raise AssertionError(f"{tier} policy did not apply for gNB {node}:\n{output_text}")
        policy_lines = [line for line in output_text.splitlines()
                        if line.startswith(f"HERO_SLICE_POLICY gnb_id={node} ")]
        if not policy_lines or f"source=ppo-{tier}" not in policy_lines[-1]:
            raise AssertionError(f"manager did not retain PPO ratios for gNB {node}:\n{output_text}")
        def ratios_from(line: str) -> tuple[float, float, float]:
            value = line.split("ratios=", 1)[1].split()[0]
            return tuple(float(part) for part in value.split(","))
        if not all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-7)
                   for a, b in zip(ratios_from(applied_lines[-1]), ratios_from(policy_lines[-1]))):
            raise AssertionError(f"final manager ratios differ from last PPO action for gNB {node}")
    if f"[HERO PPO] tier={tier}" not in output_text or "PASS native multi-gNB DL/UL measurement scenario" not in output_text:
        raise AssertionError(f"{tier} scenario did not train or complete successfully:\n{output_text}")
    if not csv_path.is_file():
        raise AssertionError(f"native measurement CSV missing: {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not any(row.get("scope") == "cell" and
               (float(row.get("dl_rx_pdcp_sdu_bytes", 0) or 0) > 0 or
                float(row.get("ul_rx_pdcp_sdu_bytes", 0) or 0) > 0)
               for row in rows):
        # Column names may evolve; actual bytes are also checked by native output.
        if "DL_SDU_bytes=" not in output_text or "UL_SDU_bytes=" not in output_text:
            raise AssertionError("scenario did not prove nonzero native traffic measurements")
    experience = checkpoint / "live-transitions.jsonl"
    if not experience.is_file() or experience.stat().st_size == 0:
        raise AssertionError(f"actual PPO transitions were not recorded: {experience}")
    with experience.open(encoding="utf-8") as stream:
        events = [json.loads(line) for line in stream if line.strip()]
    transitions = [event for event in events if event.get("record_type") == "transition"]
    updates = [event for event in events if event.get("record_type") == "update"]
    if not transitions or not updates:
        raise AssertionError(f"training journal is missing transition/update events: {experience}")
    if tier == "local":
        episodes = [event for event in events if event.get("record_type") == "episode"]
        if [event.get("episode_id") for event in episodes] != [1, 2]:
            raise AssertionError(f"local run did not emit two configured per-gNB episode records: {episodes}")
        if any(event.get("episode_steps") != 2 or event.get("simulator_reset") is not False for event in episodes):
            raise AssertionError("episodes must use two real control transitions and explicitly record no simulator reset")
        if len(transitions) != 4 or not all(
                transition.get("truncated") for transition in transitions if transition.get("episode_step") == 2):
            raise AssertionError("episode boundaries must truncate PPO GAE and stop collection at the configured cap")
    if not all("actor_loss" in event.get("metrics", {}) and
               "critic_loss" in event.get("metrics", {}) and
               event.get("update_duration_ms", 0) >= 0 for event in updates):
        raise AssertionError("PPO update journal is missing loss or duration metrics")
    if "event=request_complete" not in output_text or "avg_ms=" not in output_text:
        raise AssertionError("C++ PPO IPC latency diagnostics were not emitted")
    expected_checkpoint = checkpoint / (f"local-ue-aware-gnb-101.pt" if formulation == "ue-aware"
                                        else f"local-gnb-101.pt") if tier == "local" else None
    if expected_checkpoint is not None and not expected_checkpoint.is_file():
        raise AssertionError(f"expected {formulation} local checkpoint is missing: {expected_checkpoint}")
    if not any(checkpoint.glob("*.pt")):
        raise AssertionError(f"PPO update did not save a checkpoint under {checkpoint}")
    if formulation == "ue-aware":
        bounded = [event for event in transitions if event.get("formulation") == "ue-aware"]
        if not bounded:
            raise AssertionError("UE-aware run did not record its formulation in transition journal")
        for event in bounded:
            terms = event.get("reward_terms", {})
            if set(terms) != {"Q", "A", "E", "F", "H"} or any(not 0 <= terms[key] <= 1 for key in terms):
                raise AssertionError(f"invalid UE-aware component values: {terms}")
            bounds = event.get("reward_bounds") or {}
            reward = event.get("reward")
            if not (math.isclose(bounds.get("global_lower_bound", 1), -0.05, abs_tol=1e-9) and
                    math.isclose(bounds.get("global_upper_bound", 0), 0.95, abs_tol=1e-9) and
                    bounds.get("state_action_lower_bound", 1) <= reward <= bounds.get("state_action_upper_bound", -1)):
                raise AssertionError(f"reward violated logged analytical bounds: {event}")
        for episode in (event for event in events if event.get("record_type") == "episode"):
            if not (math.isclose(episode.get("reward_min", 1), -0.05, abs_tol=1e-9) and
                    math.isclose(episode.get("reward_max", 0), 0.95, abs_tol=1e-9) and
                    -0.05 <= episode.get("mean_reward", -1) <= 0.95 and
                    math.isclose(episode.get("reward_gap"), 0.95 - episode.get("mean_reward"), abs_tol=1e-9)):
                raise AssertionError(f"UE-aware episode mean violates its analytical reward bound: {episode}")
            if len(event.get("observation", [])) != 144 or len(event.get("next_observation", [])) != 144:
                raise AssertionError("UE-aware local observation schema must remain 144 values (24 features × 3 slices × value/mask)")
        expected_columns = ("applied_embb_ratio", "dl_slice_budget_embb", "dl_slice_used_embb", "prbs_per_rbg")
        if not all(column in rows[0] for column in expected_columns):
            raise AssertionError("CSV is missing actual applied slice budget/MAC use fields")
        for row in rows:
            if row.get("scope") != "cell":
                continue
            for name in ("embb", "urllc", "mmtc"):
                budget = float(row.get(f"dl_slice_budget_{name}") or 0)
                used = float(row.get(f"dl_slice_used_{name}") or 0)
                if used > budget:
                    raise AssertionError(f"measured {name} DL MAC use exceeded its manager budget: {used}>{budget}")
    print(f"PASS live {tier} PPO controls native LENA slice budgets")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run(args.binary.resolve(), args.output.resolve(), "local")
    run(args.binary.resolve(), args.output.resolve(), "regional")
    run(args.binary.resolve(), args.output.resolve(), "local", "ue-aware")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
