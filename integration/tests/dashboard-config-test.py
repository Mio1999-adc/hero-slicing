#!/usr/bin/env python3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
from server import DEFAULT, clean_config, sctp_listener_inode


def check(config, expected):
    _, errors = clean_config(config)
    assert bool(errors) == expected, (config, errors)


check(DEFAULT, False)
check({**DEFAULT, "runUntilStopped": "yes"}, True)
check({**DEFAULT, "gnbs": 0}, True)
check({**DEFAULT, "gnbs": 16, "ues": 16, "connectRic": False, "kpmMonitor": False, "enableControl": False}, True)
check({**DEFAULT, "gnbs": 15, "ues": 15, "connectRic": False, "kpmMonitor": False, "enableControl": False}, False)
check({**DEFAULT, "ues": 2, "gnbs": 3, "kpmMonitor": False}, True)
check({**DEFAULT, "sampleMs": 500}, True)  # Stock xApp is fixed at 1000 ms.
check({**DEFAULT, "sampleMs": 500, "kpmMonitor": False}, False)
check({**DEFAULT, "gnbs": 3, "ues": 3}, False)  # Automatically selects the supported radio-only path.
check({**DEFAULT, "gnbs": 3, "ues": 3, "kpmMonitor": False}, False)
check({**DEFAULT, "gnbs": 3, "ues": 3, "connectRic": False, "kpmMonitor": False, "enableControl": False}, False)
check({**DEFAULT, "gnbs": 15, "ues": 15}, False)  # Large topologies launch radio-only.
check({**DEFAULT, "connectRic": False}, True)  # Defaults still enable dependent xApps.
check({**DEFAULT, "connectRic": False, "kpmMonitor": False, "enableControl": False}, False)
check({**DEFAULT, "ricIp": "not-an-ip"}, True)
check({**DEFAULT, "ricPort": 36421}, True)  # E2 port is fixed, never an editable field.
check({**DEFAULT, "duration": 1.0}, True)
check({**DEFAULT, "duration": 600.5}, True)
assert sctp_listener_inode(36421, " ENDPT     SOCK   STY SST HBKT LPORT   UID INODE LADDRS\n0x1 0x2 1 10 0 36421 1000 987654 127.0.0.1\n") == "987654"
assert sctp_listener_inode(36422, " ENDPT     SOCK   STY SST HBKT LPORT   UID INODE LADDRS\n0x1 0x2 1 10 0 36421 1000 987654 127.0.0.1\n") is None
print("PASS dashboard configuration validation and capability constraints")
