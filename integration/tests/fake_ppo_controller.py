"""Small line-protocol peer used to exercise the C++ PPO IPC lifecycle."""
import os
import sys
import time


def main():
    print("READY", flush=True)
    mode = os.environ.get("HERO_FAKE_PPO_MODE", "fast")
    for line in sys.stdin:
        if line.strip() == "STOP":
            return 0
        fields = line.rstrip("\n").split("\t")
        if len(fields) != 6 or fields[0] != "SAMPLE":
            print("ERROR\tbad request", flush=True)
            continue
        request_id, _, _, id_list, _ = fields[1:]
        if mode == "exit":
            os._exit(23)
        if mode == "silent":
            time.sleep(20)
            continue
        if mode == "slow":
            for elapsed in range(10, 61, 10):
                time.sleep(0.01)
                print(f"BUSY\t{request_id}\ttraining_update\t{elapsed}", flush=True)
        actions = []
        for node in id_list.split(","):
            if node:
                actions.append(f"{node}:7:0.2,0.3,0.5")
        print("ACTION\t" + ";".join(actions), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
