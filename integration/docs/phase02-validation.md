# Phase 02 — E2 connection and native gNB registration

Validated on 2026-10-13. Acceptance passed with the installed original Near-RT RIC and an actual native LENA gNB/UE radio simulation. No FlexRIC emulator executable or emulator source is used by the implementation. No KPM indications, standard measurement mapping, subscriptions, RC control, slicing, RL or FL are implemented.

## Verified environment

| Component | Revision / profile |
|---|---|
| Native ns-3 | `ab4cce021d8f6b2458784704a10af810d3969f0f`, ns-3.42; existing debug shared build |
| Native LENA | `f13da23934df0e179dc975649b6ac74f349440a2`, v3.3.1 |
| Original FlexRIC | `f56d4487507c7ace392f1ec3f598e7eeda670acc`; existing Debug, E2AP_V1, KPM_V3_00, ASN E2AP/KPM encoding |
| Host/compiler | Ubuntu 24.04.5 LTS, GNU C/C++ 13.3.0 |

Revisions, tracked diffs, available archives/shared libraries and CMake caches were checked again. All upstream tracked diffs remain empty. Previously observed untracked files remain untouched. No upstream source, config, version, build directory or installed library was replaced or reconfigured.

## APIs and compatibility findings before implementation

| Selected API / source | Use and findings |
|---|---|
| [platforms/flexric/src/agent/e2_agent.h](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/e2_agent.h), [platforms/flexric/src/agent/e2_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/e2_agent.c): e2_init_agent/e2_start_agent/e2_free_agent | Existing per-instance agent supplies SCTP endpoint, registry, setup generation, retry timer and protocol event loop. Blocking loop runs in our owned pthread. Using this inspected internal API enables configurable port and per-instance message observers; public singleton wrapper is not used. |
| [platforms/flexric/src/agent/endpoint_agent.h](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/endpoint_agent.h), [platforms/flexric/src/agent/endpoint_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/endpoint_agent.c) | Original SCTP socket creation, socket options, send and receive APIs are retained. Lower-level initializer accepts IPv4 address and port; stock RIC listens at 36421. |
| [platforms/flexric/src/lib/ep/e2ap_ep.h](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/lib/ep/e2ap_ep.h), [platforms/flexric/src/lib/ep/e2ap_ep.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/lib/ep/e2ap_ep.c): e2ap_ep_init | Explicitly initialize endpoint mutex before worker I/O. The inspected agent endpoint initializer does not call this function. No upstream edit. |
| [/usr/include/linux/sctp.h](/usr/include/linux/sctp.h), [/usr/include/netinet/sctp.h](/usr/include/netinet/sctp.h) | Native connect/poll/SO_ERROR and SCTP_GET_PEER_ADDR_INFO/SCTP_STATUS establish a bounded association and verify SCTP_ESTABLISHED before original agent loop. SCTP_STATUS is read-only evidence, not a simulated connection flag. |
| [platforms/flexric/src/agent/gen_msg_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/gen_msg_agent.c): gen_setup_request_v1 | Original function constructs Setup Request from original plugin registry, including configured identity and encoded KPM definition. |
| [platforms/flexric/src/agent/msg_handler_agent.h](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/msg_handler_agent.h), [platforms/flexric/src/agent/msg_handler_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/msg_handler_agent.c) | Per-instance dispatch table hooks call original Setup Response/Failure handlers and then update synchronized local state. Original success handler clears the retry timer. Failure terminates this phase's agent rather than retrying indefinitely. |
| [platforms/flexric/src/agent/not_handler_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/not_handler_agent.c): notification_handle_ag | GNU link option `--wrap=notification_handle_ag` intercepts shutdown notifications for our worker, records transport failure and stops it; prevents original reconnection timer from being added after loss. Unrelated calls delegate to original handler. |
| [platforms/flexric/src/agent/plugin_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/plugin_agent.c): init_plugin_ag/load_plugin_ag | Loads only original libkpm_sm.so from a dedicated build-directory symlink. Strict plugin-directory validation prevents accidental registration of other installed models with missing callbacks. Path ends in slash because original loader concatenates filenames directly. |
| [platforms/flexric/src/sm/kpm_sm/kpm_sm_v03.00/kpm_sm_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/sm/kpm_sm/kpm_sm_v03.00/kpm_sm_agent.c), [platforms/flexric/src/sm/kpm_sm/kpm_sm_v03.00/kpm_sm_id.h](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/sm/kpm_sm/kpm_sm_v03.00/kpm_sm_id.h) | Original factory registers KPM ID 2, revision 3, OID and function name. Setup callback leaves both optional style lists absent. No report style or measurement name is invented. |
| [platforms/flexric/src/sm/kpm_sm/kpm_sm_v03.00/ie/e2sm_kpm_v03.00_standard.asn1](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/sm/kpm_sm/kpm_sm_v03.00/ie/e2sm_kpm_v03.00_standard.asn1) | Event-trigger and report-style lists are OPTIONAL. Omitting them produces a registration-only definition accepted by original RIC. It does not claim reporting capability. |
| [platforms/flexric/src/lib/e2ap/v1_01/e2ap_types/common/e2ap_global_node_id.h](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/lib/e2ap/v1_01/e2ap_types/common/e2ap_global_node_id.h), [platforms/flexric/src/util/conversions.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/util/conversions.c) | Use monolithic ngran_gNB, configured PLMN/MNC digit length, explicit 32-bit gNB ID, unused=0, no CU/DU identifier. Identity is separate from simulator node/cell IDs. |
| [platforms/ns-3/contrib/nr/helper/nr-helper.h](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/helper/nr-helper.h), [platforms/ns-3/contrib/nr/helper/nr-helper.cc](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/helper/nr-helper.cc) | Native gNB and UE installation, native BWP initialization, AssignStreams, UpdateDeviceConfigs and AttachToGnb. No Orange wiring. |
| [platforms/ns-3/contrib/nr/helper/nr-point-to-point-epc-helper.h](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/helper/nr-point-to-point-epc-helper.h), [platforms/ns-3/contrib/nr/helper/nr-no-backhaul-epc-helper.h](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/helper/nr-no-backhaul-epc-helper.h) | Native EPC/PGW and UE address allocation support actual downlink UDP delivery through the radio. |
| [platforms/ns-3/src/core/model/realtime-simulator-impl.h](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/src/core/model/realtime-simulator-impl.h), [platforms/ns-3/contrib/nr/model/nr-gnb-net-device.h](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/model/nr-gnb-net-device.h) | Example uses original realtime simulator; agent constructor retains the real gNB and Start validates native node ID/cell IDs before copying identity to protocol worker. |

The selected internal APIs are pinned to this exact checkout; this is single-gNB validation, not proof of arbitrary concurrent multi-agent safety.

## Implementation and lifecycle

`FlexricE2Agent` implements the proposed E2Agent interface and attaches directly to a native NrGnbNetDevice. Its C11 shim uses only copied endpoint/identity fields. No SCTP callback reads or mutates LENA objects or schedules radio events. State reads use a mutex; connection/setup readiness uses a condition variable. The ns-3 thread starts the protocol worker and waits for registration before Run, then stops/joins it before Simulator::Destroy.

States: Connecting, SetupPending, Registered, SetupFailed, TransportFailed, Unsupported, Stopped (C++ wrapper after release). A bounded readiness wait returns false if pending at deadline; the example reports timeout/failure and calls Stop. Setup Responses are accepted only when the sole KPM function ID 2 is accepted and no function is rejected. Failure cause is logged. SCTP loss stops the worker. Unexpected procedures fail closed and stop the association; this phase does not implement subscription/control procedures or their rejection responses. A no-indications callback is defensive and never reached by admitted subscriptions, because subscriptions are not admitted.

Connect uses the socket created by original FlexRIC, with temporarily nonblocking native association establishment and read-only SCTP status queries. It restores blocking mode before original receive operations. The existing original setup timer can retransmit while readiness is pending; failure/loss stops the loop. No automatic reconnect is implemented.

Original e2_free_agent waits for agent_stopped. After a pre-loop connection failure, our worker explicitly marks its instance stopped. On regular shutdown, Stop sets the atomic stop token and joins the worker first. Cleanup closes remaining pending timers, then invokes original free and releases original epoll/pipe descriptors that its free routine does not release. Repeated fixture tests verify descriptor count remains unchanged.

## Files created or modified in Phase 02

| File | Purpose |
|---|---|
| [integration/e2-agent/flexric-agent.h](/home/hpclab-lananh/HERO-slicing/integration/e2-agent/flexric-agent.h) | C ABI, endpoint/identity config and synchronized status contract |
| [integration/e2-agent/flexric-agent.c](/home/hpclab-lananh/HERO-slicing/integration/e2-agent/flexric-agent.c) | Original agent lifecycle, setup observers, KPM-only registration, SCTP status, identity wire check, cleanup |
| [integration/e2-agent/flexric-e2-agent.h](/home/hpclab-lananh/HERO-slicing/integration/e2-agent/flexric-e2-agent.h), [integration/e2-agent/flexric-e2-agent.cc](/home/hpclab-lananh/HERO-slicing/integration/e2-agent/flexric-e2-agent.cc) | C++ E2Agent owner attached to real gNB |
| [integration/examples/single-gnb-e2.cc](/home/hpclab-lananh/HERO-slicing/integration/examples/single-gnb-e2.cc) | One native gNB/UE, 20 MHz BWP at 3.5 GHz, native EPC, seeded downlink UDP radio simulation, configurable endpoint/PLMN/E2 ID |
| [integration/tests/e2-identity-registration.c](/home/hpclab-lananh/HERO-slicing/integration/tests/e2-identity-registration.c) | Actual encode/decode identity/KPM-definition tests and invalid identity rejection |
| [integration/tests/e2-network-lifecycle.c](/home/hpclab-lananh/HERO-slicing/integration/tests/e2-network-lifecycle.c) | Original-codec SCTP fixtures for success/failure/timeout/loss/acceptance checks and descriptor cleanup |
| [integration/tests/run-phase02.py](/home/hpclab-lananh/HERO-slicing/integration/tests/run-phase02.py) | Own original RIC/native example subprocesses, capture logs/kernel associations, assert acceptance and cleanly stop only its children |
| [integration/CMakeLists.txt](/home/hpclab-lananh/HERO-slicing/integration/CMakeLists.txt) | New shim/example/test targets; link existing archives, create original KPM-only plugin symlink; optional network CTest registration |
| [integration/README.md](/home/hpclab-lananh/HERO-slicing/integration/README.md) | Updated build/run instructions and current phase status |
| This document | Source-backed results, exact commands and limitations |

Relative to Phase 01: nine files added, CMakeLists and README modified. Root repository still contains the entire uncommitted integration tree from both phases; Git lists these as untracked, so ordinary git diff --stat does not show them. No commit, reset, upstream patch or source deletion was performed. Generated binaries, plugins and logs are ignored by existing rules.

## Exact reproducible commands

From `/home/hpclab-lananh/HERO-slicing`:

```bash
integration/tests/run-smoke.sh
/usr/bin/python3 integration/tests/run-phase02.py   --logs integration/logs/phase02/final-validation
```

The first command executes:

```bash
cmake -S integration -B integration/build
cmake --build integration/build -j2
ctest --test-dir integration/build --output-on-failure
```

The second executes identity and loopback fixture tests, then these actual RIC/native commands (the runner prefixes the native command with stdbuf to preserve timely logs):

```bash
integration/build/e2-identity-registration
integration/build/e2-network-lifecycle   /home/hpclab-lananh/HERO-slicing/integration/build/phase02-plugins

stdbuf -oL -eL   /home/hpclab-lananh/HERO-slicing/platforms/flexric/build/examples/ric/nearRT-RIC   -c /usr/local/etc/flexric/flexric.conf   -p /home/hpclab-lananh/HERO-slicing/integration/build/phase02-plugins/   -a 127.0.0.1

integration/build/single-gnb-e2   --ricIp=127.0.0.1 --ricPort=36421 --gnbId=101   --mcc=505 --mnc=1 --mncDigits=2 --duration=2 --realtime=1
```

Host SCTP tests were run outside the socket-restricted sandbox. They need normal host SCTP access, not root privileges. The stock RIC port is hardcoded at 36421 (and its iApp port at 36422); the agent itself accepts a configurable port, exercised by ephemeral-port fixtures. Runner checks port availability first and refuses to interfere with an existing service. It sends SIGINT to its own RIC after native completion and verifies exit 0. The RIC and native processes were stopped after validation.

Additional original focused regression commands:

```bash
ctest --test-dir platforms/flexric/build   -R 'Unit_test_enc_dec_(E2AP|KPM|RC)' --output-on-failure
platforms/ns-3/build/utils/ns3.42-test-runner-debug --suite=nr-test-sched
platforms/ns-3/build/utils/ns3.42-test-runner-debug --suite=nr-test-notching
```

## Runtime evidence and results

Final saved evidence:

- [integration/logs/phase02/final-validation/native-lena.log](/home/hpclab-lananh/HERO-slicing/integration/logs/phase02/final-validation/native-lena.log): actual native gNB/UE identity, SCTP establishment, Setup Request/Response, accepted KPM ID and radio delivery.
- [integration/logs/phase02/final-validation/ric.log](/home/hpclab-lananh/HERO-slicing/integration/logs/phase02/final-validation/ric.log): original RIC receipt, PLMN/gNB identity, accepted KPM, removal at disconnect and successful RIC shutdown.
- [integration/logs/phase02/final-validation/sctp-associations.log](/home/hpclab-lananh/HERO-slicing/integration/logs/phase02/final-validation/sctp-associations.log): sampled kernel association table while the native example was running.
- [integration/logs/phase02/final-validation/network-lifecycle.log](/home/hpclab-lananh/HERO-slicing/integration/logs/phase02/final-validation/network-lifecycle.log): all twelve fixture runs and descriptor counts.
- [integration/logs/phase02/final-validation/identity-registration.log](/home/hpclab-lananh/HERO-slicing/integration/logs/phase02/final-validation/identity-registration.log): wire roundtrip and registration tests.
- [integration/build.log](/home/hpclab-lananh/HERO-slicing/integration/build.log), [integration/test.log](/home/hpclab-lananh/HERO-slicing/integration/test.log), [integration/configure.log](/home/hpclab-lananh/HERO-slicing/integration/configure.log): final build and ordinary CTest output.

Actual final acceptance evidence:

```text
[NATIVE LENA] ns-3.42 / LENA v3.3.1 gNB node=0 cell=1 BWPs=1 UE IMSI=1 E2 gNB_ID=101
[HERO E2] identity PLMN=50501 gNB_ID=101 bits=32 RAN=ngran_gNB registry_count=1 KPM_ID=2 revision=3
[HERO E2] SCTP_ESTABLISHED assoc_id=47 peer=127.0.0.1:36421 in_streams=10 out_streams=10
[E2-AGENT]: E2 SETUP-REQUEST tx
[E2AP]: E2 SETUP-REQUEST rx from PLMN 50501 Node ID 101 RAN type ngran_gNB
[NEAR-RIC]: Accepting RAN function ID 2 with def = ORAN-E2SM-KPM
[E2-AGENT]: E2 SETUP RESPONSE rx
[HERO E2] E2_SETUP_RESPONSE accepted RAN function ID=2
[NATIVE LENA] simulated_seconds=2 UDP_DL_received=100 UE_RRC_state=9 radio=PASS E2=REGISTERED
[HERO E2] shutdown joined worker and released SCTP/epoll/pipe/timers
[NEAR-RIC]: Removing E2 Node MCC 505 MNC 01 NB_ID 101
The nearRT-RIC run SUCCESSFULLY
```

The excerpt combines lines from the two logs by procedure, not a globally timestamped trace. Kernel table shows agent association 47 and RIC-side association 48, with agent local port 42278 and RIC port 36421. IDs/ephemeral ports vary per run. UE RRC state 9 is CONNECTED_NORMALLY in the inspected enum. Packet receipt is native UDP delivery across LENA, not a KPM indication.

| Check | Final result |
|---|---|
| Existing Phase-01 compilation smoke | PASS |
| Identity roundtrips: 2-digit MNC, 3-digit MNC, leading-zero PLMN; invalid MNC/gNB ID | PASS |
| Encoded KPM definition ID 2, revision 3, absent styles | PASS |
| Native radio-only default CTest | PASS |
| Six network fixture scenarios, repeated twice | 12/12 PASS; every fd count 4 before / 4 after |
| Original RIC + native gNB/UE real association | PASS; both exit 0; 100 UDP packets received |
| Existing selected FlexRIC CTest labels | 3/3 PASS |
| Native scheduler / notching suites | PASS / PASS |

Fixtures cover accepted setup, valid E2 Setup Failure, unanswered setup bounded wait, unavailable endpoint, graceful peer shutdown after registration and response accepting a wrong function ID. They validate error paths but do not substitute for the original-RIC/native acceptance run. Network fixtures can optionally be registered in CTest with `-DHERO_ENABLE_NETWORK_TESTS=ON`; default CTest omits host-socket tests.

## Findings during compilation/validation and remaining limits

1. Original V1 Setup Failure encoder bug: [platforms/flexric/src/lib/e2ap/v1_01/enc/e2ap_msg_enc_asn.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/lib/e2ap/v1_01/enc/e2ap_msg_enc_asn.c) assigns `ProtocolIE_ID_id_E2connectionSetupFailed` (40) to the Cause field. Decoder [platforms/flexric/src/lib/e2ap/v1_01/dec/e2ap_msg_dec_asn.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/lib/e2ap/v1_01/dec/e2ap_msg_dec_asn.c) requires `ProtocolIE_ID_id_Cause` (1). The first failure fixture aborted on this mismatch. The corrected test fixture uses original PDU builder, sets the field to the installed Cause constant, and uses original PDU encoder/free helpers. Production and upstream source are unchanged. Valid wire failure reception is now tested. Receiving that original malformed failure may still abort in the original decoder.
2. Stock original RIC's V1 generate_setup_failure is not implemented: [platforms/flexric/src/ric/generate_setup_failure.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/ric/generate_setup_failure.c). Therefore valid failure reception is exercised with an explicitly labelled fixture; success is validated with the real original RIC. The existing V1 CTest label points to test_e2ap_encode_asn, not the separately named encode/decode binary: [platforms/flexric/test/encode_decode/e2ap/v1_01/CMakeLists.txt](/home/hpclab-lananh/HERO-slicing/platforms/flexric/test/encode_decode/e2ap/v1_01/CMakeLists.txt). Its pass did not establish failure wire compatibility.
3. Initial association-status code attempted an unavailable helper symbol; the compiler/linker caught it. It was removed and replaced with inspected Linux SCTP_GET_PEER_ADDR_INFO/SCTP_STATUS APIs. No unsupported helper remains. Fixture reply message type is const in installed headers; initialization was corrected to use the actual type contract.
4. The original static e2_agent object still produces the executable-stack linker warning found in Phase 01. It is neither patched nor suppressed. An upstream header also emits its prior unused-parameter warning.
5. Original parser/event-loop code uses assertions for malformed messages and some transport edge cases. Graceful peer shutdown, unavailable endpoint and normal setup failure are tested; abrupt loss, fuzzed messages, unusual optional failure diagnostics and remote-host networking are not claimed validated.
6. Only one gNB/UE and one BWP is validated. No in-process multi-gNB concurrency, reconnect policy, CU/DU identity modes, KPM reports, subscriptions or RC controls are implemented. The registered KPM function intentionally has no report styles. Realtime simulation uses best effort and is not a hard realtime guarantee.

Phase 02 stops here; no work on measurements/indications or control proceeds automatically.
