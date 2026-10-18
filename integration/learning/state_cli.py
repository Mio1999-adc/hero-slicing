"""Convert native measurement CSV windows to fixed-schema PPO observation JSONL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .state import HeroStateBuilder, read_measurement_windows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("local", "regional"), required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-gnbs", type=int, default=1)
    args = parser.parse_args(argv)
    builder = HeroStateBuilder()
    windows = read_measurement_windows(args.csv)
    by_sequence: dict[int, list] = {}
    for (sequence, _gnb), rows in sorted(windows.items()):
        by_sequence.setdefault(sequence, []).append(builder.build_local(rows))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for sequence in sorted(by_sequence):
            local = sorted(by_sequence[sequence], key=lambda x: x.gnb_id)
            if args.tier == "local":
                for obs in local:
                    record = {"tier": "local", "sequence": obs.sequence,
                              "gnb_id": obs.gnb_id, "simulation_time_ns": obs.end_sim_ns,
                              "observation": obs.values}
                    stream.write(json.dumps(record, allow_nan=False) + "\n")
            else:
                if len(local) > args.max_gnbs:
                    parser.error(f"sample {sequence} has {len(local)} gNBs; --max-gnbs is {args.max_gnbs}")
                obs = builder.build_regional(local, args.max_gnbs)
                record = {"tier": "regional", "sequence": obs.sequence,
                          "node_ids": obs.gnb_ids, "simulation_time_ns": obs.end_sim_ns,
                          "observation": obs.values}
                stream.write(json.dumps(record, allow_nan=False) + "\n")
    print(json.dumps({"tier": args.tier, "windows": len(by_sequence),
                      "observations_written": sum(len(v) for v in by_sequence.values()) if args.tier == "local" else len(by_sequence),
                      "source_csv": str(Path(args.csv)), "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
