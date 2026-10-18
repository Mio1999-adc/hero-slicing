# Phase 07 — integration and packaging validation

Validated 2026-10-15 on Ubuntu 24.04 in `/home/hpclab-lananh/HERO-slicing`. The package builds against the installed native ns-3.42 / NR-LENA v3.3.1 trees and original FlexRIC revision `f56d4487507c7ace392f1ec3f598e7eeda670acc` configured for E2AP v1 and KPM v3. The integration is independent of Orange source code and does not modify the upstream trees.

## Commands and results

The reproducible full command is:

```sh
./integration/scripts/run-phase07-validation.sh
```

It completed with `PASS Phase 07 validation suite`. Its stages produced these results:

| Stage | Result |
|---|---|
| Integration configure/build and CTest | 8/8 passed: compile/link smoke, E2 identity, KPM subscription compatibility, radio-only operation, native measurement aggregation, multi-gNB aggregation, real-traffic measurement verification, private RC validation/ASN round-trip |
| Independent ns-3 build | `platforms/ns-3/ns3 build core nr -j 2` passed in the existing build tree |
| Focused ns-3 tests | `nr-test-sched` and `nr-test-notching` passed |
| Independent FlexRIC build | `cmake --build platforms/flexric/build -j 2` passed, including Near-RT RIC and stock xApps |
| FlexRIC tests | 13/13 selected tests passed; see the upstream test caveat below |
| E2 setup and lifecycle | Native gNB 101 registered with PLMN 50501 and KPM RAN function ID 2; fixture cases covered Setup success/failure, timeout, refused connection, peer shutdown, missing function acceptance, and descriptor cleanup |
| KPM end-to-end | Stock FlexRIC monitor accepted the subscription, decoded periodic indications, and deleted its subscription cleanly |
| Two-gNB KPM/control | Both native gNBs registered; independent KPM streams decoded; private control targeted gNB 102, applied `MaxDlMcs=1` at simulation time 387,000,000 ns, and received a control acknowledgement |
| Failure isolation | Refused connection for gNB 102 did not prevent gNB 101 from registering and completing radio simulation |

The measured KPM comparison from the single-gNB run was native sample sequence 1: DL `1.8144 Mbps`, UL `0.674784 Mbps`; the stock xApp decoded `1.81 Mbps` and `0.67 Mbps` for the same indication. In the two-gNB run, four KPM E2AP APER user messages were captured at 160 bytes each, 640 bytes total, excluding SCTP/IP framing. The realtime eight-simulated-second two-gNB scenario took 128.676 wall-clock seconds on this host. These overhead figures are a single-run observation, not a performance guarantee.

## Upstream test caveat

The full original FlexRIC CTest list contains 14 tests. `Unit_test_nearRT_RIC` reproducibly aborts in the unmodified upstream `src/ric/pending_events.c` at assertion `valid_pending_event` (observed on two elevated runs). It was also tried separately. The Phase 07 runner reports and excludes this one known test so the remaining 13 can run and the E2 integration scenarios can complete. All other 13 FlexRIC CTests passed with SCTP-capable host permissions. Running these network tests inside the restricted sandbox instead fails earlier at `endpoint_ric.c:39` because SCTP socket creation is unavailable there; the full validation was therefore run with local SCTP access.

Three-gNB radio operation and Setup registration have been observed in Phase 06, but the installed stock FlexRIC monitor aborts while processing three concurrent subscriptions. The acceptance evidence in this report is therefore for two-gNB indication and control operation. Automatic E2 reconnection is disabled and was not claimed; shutdown and reconnection behavior remains a documented limitation.

The tested scheduler control is an explicitly private HERO RC extension (style 32767), not an operation supported by the installed standardized E2SM-RC implementation. It changes the exposed LENA `MaxDlMcs` attribute and does not modify scheduler internals or policy. No slicing, DRL, or federated-learning behavior is included.

## Logs and artifacts

The full run writes artifacts under `integration/build/`:

- `phase02-validation/` — single-gNB Setup and lifecycle fixture logs.
- `phase04-validation/` — RIC, native sample CSV/log, stock KPM xApp, and SCTP association snapshots.
- `phase06-validation/` — two-gNB RIC/native/KPM/control logs, measurements, association snapshots, and `overhead.json`.
- `phase06-isolation/` — independent failed-agent scenario logs.

Start commands, local RIC configuration, xApp launchers, and troubleshooting are in [installation and execution](installation-execution.md) and [troubleshooting](troubleshooting.md). Protocol, measurement, and control boundaries are documented in [E2 protocol workflow](e2-protocol-workflow.md), [measurement mapping](measurement-mapping.md), and [control capabilities](control-capabilities.md).

## Source integrity

No tracked changes were present in `platforms/ns-3` or `platforms/flexric` after validation. Existing untracked files in those trees (`platforms/ns-3/default` and the two FlexRIC `.bak` files listed in [API compatibility](api-compatibility.md)) were preserved. Phase 07 files and scripts are confined to `integration/`; Orange is mentioned only in explanatory documentation, and there is no Orange source, build, or runtime dependency.
