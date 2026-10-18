# Phase 06 — multi-gNB validation

## Implementation

`native-measurements` accepts `--gnbs=1..3`, `--ues=N`, `--enableE2=1`, and `--enableControl=1`. Each native gNB gets its own `LenaMeasurementProvider`, `FlexricE2Agent`, KPM read sequence, PLMN/gNB identity, SCTP association, status, and simulation-event control queue. gNB IDs are `101 + index` in this example. UE contexts are associated with the provider for their actual serving cell. The one-gNB placement and workload remain the Phase 03 baseline.

The E2 agent continues to use original FlexRIC's `e2_agent_t`, KPM v3 and RC libraries/codecs. It owns separate protocol workers and per-worker callback context. The integration wraps original `e2ap_send_sctp_msg` at link time only to count the encoded APER user-message bytes emitted immediately after each KPM measurement callback; it calls the original send function unchanged. This size excludes SCTP/IP framing and retransmissions.

The control xApp uses an explicitly private, local RC extension (style 32767; see [control-capabilities.md](control-capabilities.md)). This is not a standard E2SM-RC scheduler operation. The request names an E2 node in the original xApp API, and the selected agent's event queue applies `MaxDlMcs` on simulation time. No LENA scheduler internals or policy were changed.

## Reproducible commands

Build the independent integration tree:

```sh
cmake -S integration -B integration/build
cmake --build integration/build -j2
ctest --test-dir integration/build --output-on-failure
```

Run the full two-gNB native/FlexRIC/KPM/control test (the runner starts and stops the installed original Near-RT RIC, stock KPM monitor xApp, and local private-control xApp):

```sh
python3 -u integration/tests/run-phase06.py --duration=8 --gnbs=2 --ues-per-gnb=2 --logs=integration/build/phase06-validation-sized
```

The live run requires local SCTP loopback permissions. It binds FlexRIC on `127.0.0.1:36421` and uses the integration's KPM v3 and Phase 06 plugin build.

## Two-gNB result

Run completed successfully on 2026-10-14 with native ns-3/5G-LENA and installed FlexRIC. The RIC independently registered PLMN 505/01 gNB IDs 101 and 102; both accepted KPM function ID 2 and the local RC function ID 3. Two independent SCTP associations were observed. The stock KPM monitor decoded four indications, two per gNB, from actual traffic.

The values match the native samples by node and interval. Each indication's encoded E2AP APER user message was **160 bytes** (KPM indication plus E2AP framing, excluding SCTP/IP framing). There were four indication messages and 640 APER user-message bytes total. The measured interval was one simulated second. The example delivered 6,000 DL and 4,288 UL UDP packets over four UEs; each UE's CSV rows map to the corresponding native serving cell.

The xApp selected gNB 102 and requested `MaxDlMcs=1` for BWP 0. Its agent applied the request at simulation time `395,000,000 ns`; the target gNB's subsequent DL MCS samples were at most 1, while gNB 101 retained samples above 1. Thus the demonstrated control affected only its intended cell. The agent output contains the application result; FlexRIC's generic E2AP ACK is transport-level success and must not be used alone as proof of application.

## Overhead sample

The same run simulated 8 seconds in 120.565 seconds of wall time, or 15.071 wall seconds per simulated second. Both Setup responses were observed within 0.1 seconds of native process startup (the runner samples at 100 ms, so this is an upper bound). Four indications were encoded and all four were decoded. RSS was sampled every ~100 ms; peak observed resident memory was 81,164 KiB for native LENA, 4,540 KiB for the RIC, 8,728 KiB for the monitor xApp, and 8,312 KiB for the control xApp. Native process CPU accumulated 240.04 seconds across its threads during the 120.565-second run; the RIC and xApp CPU values rounded below 0.05 seconds at the runner's 100 ms sampling cadence. Brief memory peaks may be missed. These are one-run baseline measurements, not performance guarantees.

Detailed output is in `integration/build/phase06-validation-sized/overhead.json`, `native-lena.log`, `ric.log`, `xapp-kpm-monitor.log`, `xapp-hero-control.log`, `sctp-associations.log`, and `measurements.csv`.

## Three-gNB attempt and limits

The native radio-only configuration `--gnbs=3 --ues=9 --sampleMs=100 --duration=2` passed with 900 DL and 720 UL packets, 20 measurement windows per gNB, and correct serving-cell association for all nine UEs. Separately, with `--gnbs=3 --ues-per-gnb=2`, the RIC registered all three native identities (101, 102, 103), and the private control xApp discovered all three and routed its request to gNB 102. The installed stock KPM monitoring xApp then aborted while handling the concurrent subscriptions: its E42 event loop asserted after a pending report wait and disconnected. The RIC logged SCTP send failures and removed pending subscriptions. This is not a successful three-gNB reporting validation. The three-gNB scenario remains configurable, and setup was observed, but Phase 06's full multi-gNB indication acceptance is established for two gNBs only.

The Linux sandbox denied `tcpdump` capture on loopback (`Operation not permitted`); exact message sizes above instead come from the original FlexRIC send boundary. The focused `run-phase06-isolation.py` test directs gNB 102 to an unused SCTP port while gNB 101 connects to the real RIC. It passed: only gNB 101 sent Setup and registered; gNB 102 entered its own failed state; both providers continued collecting and the native radio scenario completed normally. This verifies per-node failure isolation and cleanup, though it does not exercise KPM subscription/report continuity on the healthy agent in that failure test. No KPM or RC wire behavior was added to FlexRIC itself.

## Test result

`cmake --build integration/build -j2` succeeded. `ctest --test-dir integration/build --output-on-failure` passed the interface smoke test, identity registration, KPM subscription compatibility, radio-only mode, single-gNB measurements, two-gNB native measurements, and deterministic Phase 03 measurement verification. The live two-gNB run above passed Setup, subscriptions, native measurement mapping, APER length capture, targeted scheduler application, and post-control isolation.
