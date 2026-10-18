#!/usr/bin/env python3
"""Matched-seed baseline-vs-UE-aware local PPO experiment (one gNB only)."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import subprocess
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def run(binary: Path, output: Path, formulation: str, seed: int, duration: float) -> dict:
    run_dir = output / f"{formulation}-seed-{seed}"
    run_dir.mkdir(parents=True, exist_ok=False)
    csv_path = run_dir / "measurements.csv"
    checkpoint_dir = run_dir / "checkpoints"
    experience_path = run_dir / "transitions.jsonl"
    command = [str(binary), "--gnbs=1", "--ues=3", f"--duration={duration:g}",
        "--sampleMs=200", f"--seed={seed}", f"--rngRun={seed}", f"--csv={csv_path}",
        "--sliceAssignment=explicit", "--embbUes=1", "--urllcUes=1", "--mmtcUes=1",
        "--embbRateBps=1000000", "--enableSliceEnforcement=1", "--enablePpo=1",
        "--ppoTier=local", f"--ppoFormulation={formulation}", "--ppoMode=train",
        "--ppoUpdateEvery=8", f"--ppoSeed={seed}", f"--ppoCheckpointDir={checkpoint_dir}",
        f"--ppoExperienceFile={experience_path}", "--enableE2=0", "--realtime=0"]
    started = time.perf_counter()
    result = subprocess.run(command, text=True, capture_output=True, timeout=max(180, duration * 30))
    wall_seconds = time.perf_counter() - started
    (run_dir / "simulation.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{formulation} seed {seed} failed; inspect {run_dir / 'simulation.log'}")
    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    cell_rows = [row for row in rows if row.get("scope") == "cell"]
    ue_rows = [row for row in rows if row.get("scope") == "ue"]
    if not cell_rows or not ue_rows:
        raise RuntimeError(f"{formulation} seed {seed} produced no native cell/UE rows")
    per_window = []
    for cell in cell_rows:
        t = int(cell["end_sim_ns"]) / 1e9
        value = float(cell.get("dl_delivered_sdu_throughput_mbps") or 0.0)
        same = [row for row in ue_rows if row["sequence"] == cell["sequence"]]
        satisfactions = []
        for row in same:
            if float(row.get("connected_ues") or 0) <= 0:
                continue
            target = float(row.get("required_throughput_bps") or 0)
            if target <= 0:
                target = float(row.get("offered_rate_bps") or 0)
            if target <= 0:
                continue
            achieved = float(row.get("dl_delivered_sdu_throughput_mbps") or 0) * 1e6
            satisfactions.append(min(1.0, max(0.0, achieved / target)))
        per_window.append({"formulation": formulation, "seed": seed, "sim_s": t,
                           "dl_throughput_mbps": value,
                           "ue_sla_satisfaction": statistics.fmean(satisfactions) if satisfactions else math.nan})
    delay_samples = [float(row["dl_pdcp_delay_mean_ns"]) / 1e6 for row in ue_rows
                     if row.get("dl_pdcp_delay_mean_ns") not in (None, "") and
                     float(row.get("connected_ues") or 0) > 0]
    events = [json.loads(line) for line in experience_path.read_text(encoding="utf-8").splitlines() if line]
    transitions = [event for event in events if event.get("record_type") == "transition"]
    updates = [event for event in events if event.get("record_type") == "update"]
    return {"formulation": formulation, "seed": seed, "wall_seconds": wall_seconds,
        "mean_dl_throughput_mbps": statistics.fmean(item["dl_throughput_mbps"] for item in per_window),
        "mean_ue_sla_satisfaction": statistics.fmean(item["ue_sla_satisfaction"] for item in per_window
                                                        if math.isfinite(item["ue_sla_satisfaction"])),
        "mean_dl_pdcp_delay_ms": statistics.fmean(delay_samples) if delay_samples else None,
        "transitions": len(transitions), "updates": len(updates),
        "mean_update_ms": statistics.fmean(event["update_duration_ms"] for event in updates) if updates else None,
        "samples": per_window, "run_directory": str(run_dir)}


def summarize(values: list[float]) -> dict:
    return {"mean": statistics.fmean(values),
            "sample_stddev": statistics.stdev(values) if len(values) > 1 else 0.0,
            "n": len(values)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[11, 23, 37])
    parser.add_argument("--duration", type=float, default=8.0)
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds) or args.duration <= 1:
        parser.error("provide unique seeds and a duration > 1 second")
    args.output.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    results = []
    for seed in args.seeds:
        for formulation in ("baseline", "ue-aware"):
            result = run(binary, args.output, formulation, seed, args.duration)
            results.append(result)
            print(f"PASS formulation={formulation} seed={seed} transitions={result['transitions']} "
                  f"updates={result['updates']} mean_DL_Mbps={result['mean_dl_throughput_mbps']:.5f} "
                  f"mean_UE_SLA={result['mean_ue_sla_satisfaction']:.5f}")
    summaries = {}
    for formulation in ("baseline", "ue-aware"):
        subset = [record for record in results if record["formulation"] == formulation]
        summaries[formulation] = {key: summarize([float(record[key]) for record in subset if record[key] is not None])
            for key in ("mean_dl_throughput_mbps", "mean_ue_sla_satisfaction", "mean_dl_pdcp_delay_ms", "wall_seconds")}
    serializable = [{key: value for key, value in record.items() if key != "samples"} for record in results]
    report = {"scenario": {"gnbs": 1, "ues": 3, "duration_seconds": args.duration,
        "sample_interval_ms": 200, "slice_assignment": "one UE per slice", "same_seed_pairs": args.seeds},
        "formulation_summaries": summaries, "runs": serializable,
        "interpretation": "Exploratory matched-seed result; do not infer convergence improvement from this report alone."}
    (args.output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    with (args.output / "window-metrics.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = ("formulation", "seed", "sim_s", "dl_throughput_mbps", "ue_sla_satisfaction")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerows(result["samples"])

    figure, axes = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
    colors = {"baseline": "#2563eb", "ue-aware": "#059669"}
    names = {"baseline": "Legacy baseline", "ue-aware": "UE-aware local PPO"}
    for formulation in ("baseline", "ue-aware"):
        group = [record for record in results if record["formulation"] == formulation]
        times = sorted({point["sim_s"] for record in group for point in record["samples"]})
        for axis, key, ylabel in ((axes[0], "ue_sla_satisfaction", "UE throughput-target satisfaction"),
                                  (axes[1], "dl_throughput_mbps", "DL PDCP SDU throughput (Mbit/s)")):
            means, deviations = [], []
            for t in times:
                values = [next((point[key] for point in record["samples"] if point["sim_s"] == t), math.nan)
                          for record in group]
                values = [value for value in values if math.isfinite(value)]
                means.append(statistics.fmean(values) if values else math.nan)
                deviations.append(statistics.stdev(values) if len(values) > 1 else 0.0)
            axis.plot(times, means, color=colors[formulation], label=names[formulation])
            axis.fill_between(times, [m-d for m,d in zip(means,deviations)],
                              [m+d for m,d in zip(means,deviations)], color=colors[formulation], alpha=0.16)
            axis.set_ylabel(ylabel)
            axis.grid(True, alpha=0.25)
    axes[0].legend()
    axes[1].set_xlabel("Simulation time (s)")
    figure.suptitle("Matched-seed local PPO comparison · single gNB")
    figure.tight_layout()
    figure.savefig(args.output / "convergence-common-metrics.png", dpi=160)
    print(f"Wrote {args.output / 'summary.json'} and {args.output / 'convergence-common-metrics.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
