"""Train/infer using explicitly supplied real transition/observation JSONL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import torch

from .hero_ppo import PpoConfig, SliceRatioPPO, Transition, validate_ratio_groups
from .state import OBSERVATION_DIM


def _read_jsonl(path: str) -> list[dict]:
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("train", "infer"))
    parser.add_argument("--tier", choices=("local", "regional"), required=True)
    parser.add_argument("--input", required=True, help="JSONL of real transitions or observations")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--max-gnbs", type=int, default=1)
    parser.add_argument("--output", help="JSONL output for training metrics or inference actions")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    groups = 1 if args.tier == "local" else args.max_gnbs
    if args.tier == "regional" and groups < 1:
        parser.error("--max-gnbs must be positive")
    observation_dim = OBSERVATION_DIM if args.tier == "local" else (OBSERVATION_DIM + 1) * groups
    checkpoint = Path(args.checkpoint)
    records = _read_jsonl(args.input)
    config = PpoConfig(seed=args.seed)
    if checkpoint.exists():
        saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
        config_values = dict(saved.get("config", {}))
        if "hidden_sizes" in config_values:
            config_values["hidden_sizes"] = tuple(config_values["hidden_sizes"])
        config = PpoConfig(**config_values)
    agent = SliceRatioPPO(observation_dim, groups, config)
    if args.mode == "train":
        if not records:
            parser.error("training input contains no transitions")
        transitions = [Transition(tuple(r["observation"]), tuple(tuple(x) for x in r["action"]),
                                  float(r["old_log_probability"]), float(r["reward"]),
                                  tuple(r["next_observation"]), bool(r.get("terminated", False)),
                                  bool(r.get("truncated", False)), int(r.get("simulation_time_ns", 0)),
                                  int(r.get("policy_version", 0)), tuple(r.get("node_ids", ())))
                       for r in records]
        if any(t.policy_version != transitions[0].policy_version for t in transitions):
            parser.error("one PPO batch must use a consistent policy_version")
        if checkpoint.exists():
            agent.load(checkpoint)
        metrics = agent.update(transitions)
        agent.save(checkpoint, {"tier": args.tier, "reward_is_experimental": True})
        output = {"tier": args.tier, "policy_version": agent.policy_version,
                  "transition_count": len(transitions), "metrics": metrics,
                  "checkpoint": str(checkpoint), "learning_data_source": "supplied transition JSONL"}
        print(json.dumps(output, sort_keys=True))
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.output).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(output, sort_keys=True) + "\n")
        return 0
    if not checkpoint.exists():
        parser.error("inference requires an existing checkpoint")
    agent.load(checkpoint)
    for item in records:
        observation = item["observation"]
        if args.tier == "local":
            result = agent.act(observation, deterministic=True)
            response = {"tier": "local", "gnb_id": item.get("gnb_id"),
                        "ratios": result.ratios[0], "action_status": "requested_not_applied",
                        "policy_version": result.policy_version}
        else:
            node_ids = tuple(int(n) for n in item["node_ids"])
            result = agent.act(observation, deterministic=True, active_groups=len(node_ids))
            ratios = validate_ratio_groups(result.ratios, len(node_ids))
            response = {"tier": "regional", "actions": {str(node): ratios[i].tolist()
                         for i, node in enumerate(node_ids)},
                        "action_status": "requested_not_applied",
                        "policy_version": result.policy_version}
        line = json.dumps(response, sort_keys=True, allow_nan=False)
        print(line)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.output).open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
