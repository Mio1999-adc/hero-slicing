# Control capabilities and gaps

Phase 01 implemented no control changes. Phase 05 confirmed the original RC service model has no standard scheduler-setting action. Phase 06 adds only the explicitly private extension documented below; it is not an O-RAN-standard RC control style. No network slicing or HERO method is introduced.

## Installed RC path

Original FlexRIC RC v1.03 supports decoding control headers/messages and invoking the RAN write_ctrl callback: [platforms/flexric/src/sm/rc_sm/rc_sm_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/sm/rc_sm/rc_sm_agent.c), [platforms/flexric/src/sm/rc_sm/dec/rc_dec_asn.h](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/sm/rc_sm/dec/rc_dec_asn.h). The callback's decoded `rc_ctrl_req_data_t` is freed upon return. The emulator setup advertises style 1, action 2, QoS flow mapping configuration, and its handler parses/prints DRB ID and QoS-flow parameters without mutating a scheduler: [platforms/flexric/examples/emulator/agent/sm_rc.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/examples/emulator/agent/sm_rc.c).

This example does not authorize substituting MCS or resource masks for QoS flow mapping. Other RC ASN.1 definitions do not prove supported action semantics. A future selected action must have exact style/action/parameter IDs and ranges backed by installed structures and the applicable standard, plus native implementation and execution tests.

## Native scheduler candidates

Declarations and setter bodies: [platforms/ns-3/contrib/nr/model/nr-mac-scheduler-ns3.h](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/model/nr-mac-scheduler-ns3.h), [platforms/ns-3/contrib/nr/model/nr-mac-scheduler-ns3.cc](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/model/nr-mac-scheduler-ns3.cc).

| Native parameter | Source behavior / application conditions | RC mapping and validation status |
|---|---|---|
| SetDlNotchedRbgMask / SetUlNotchedRbgMask | Copies vector; OFDMA/TDMA consult mask during scheduling. Empty means unrestricted; 1 permits an RBG, 0 excludes. Nonempty size must equal bandwidth in RBGs. Validate binary values and sufficient capacity; apply between scheduling events. | Promising cell/BWP resource constraint, not slice allocation or a proved standard RC action. No runtime modification test in Phase 01. |
| SetMaxDlMcs | Assigns cap used when processing DL CQI; existing UE MCS may persist until subsequent CQI processing. Validate against active AMC table. | Candidate parameter, not instantaneous change to every UE; no approved RC mapping. |
| SetFixedDlMcs / SetFixedUlMcs | Assigns fixed-MCS flag; CQI handler returns early when fixed. Does not rewrite all current UE states. | Simulation-thread assignment is mechanically safe; immediate desired behavior is not proven. |
| SetStartMcsDl / SetStartMcsUl | Assigns default used when configuring UEs and related CQI logic. Existing configured UE state is distinct. | Configuration/default parameter; do not claim global runtime MCS actuation. |
| SetCqiTimerThreshold | Sets CQI lifetime used by later processing | Existing timers and UE behavior need scenario validation; no RC mapping. |
| SetDlCtrlSyms / SetUlCtrlSyms / SRS timing setters | Scheduling structure/timing settings | Defer live use until consistent PHY/MAC timing and in-flight allocations are validated. |
| SetLcSched / scheduler TypeId | Scheduler/helper setup, lifecycle-dependent state | No live scheduler replacement capability selected. |

Mask consumers: [platforms/ns-3/contrib/nr/model/nr-mac-scheduler-ofdma.cc](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/model/nr-mac-scheduler-ofdma.cc), [platforms/ns-3/contrib/nr/model/nr-mac-scheduler-tdma.cc](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/model/nr-mac-scheduler-tdma.cc). CQI implementation: [platforms/ns-3/contrib/nr/model/nr-mac-scheduler-cqi-management.cc](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/model/nr-mac-scheduler-cqi-management.cc). Existing tests: [platforms/ns-3/contrib/nr/test/nr-test-notching.cc](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/test/nr-test-notching.cc), [platforms/ns-3/contrib/nr/test/nr-test-sched.cc](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/test/nr-test-sched.cc); these passing upstream suites do not establish behavior of an E2 runtime update.

A valid runtime candidate must be applied on the simulation thread at a documented boundary, use the correct concrete scheduler, validate BWP key/width and parameter ranges, preserve pending HARQ allocations, and be tested against actual later allocations. None is exposed by the read-only Phase-01 adapter.

## Deferred-control outcome gap

The installed E2AP control handler requires an ACK request and returns a SUCCESS status after the synchronous callback: [platforms/flexric/src/agent/msg_handler_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/msg_handler_agent.c). A queued task is not an applied operation. RcControlHandler distinguishes Unsupported, Rejected, Queued, Applied and Failed internally, but these are local states, not wire outcome enums.

Before the control phase, choose and validate an original-FlexRIC-compatible delayed response arrangement or a documented acceptance outcome with a valid later completion mechanism. Never block the simulation thread; bound any protocol-thread wait, handle shutdown and missing simulator progress, and do not produce a success ACK for an unvalidated/unapplied operation. Internal agent APIs may help but are not a verified solution. Do not silently patch upstream behavior or invent RC outcome fields. This unresolved contract is explicitly outside Phase 01.

For any future request: copy selected decoded parameters, validate E2 identity/cell/BWP/UE, enqueue owned data, revalidate context generation at execution, record simulation timestamp and observed scheduler result, and complete correlation tracking. Failure/no-op paths require focused tests before advertisement.

## Phase 05 compatibility result

Phase 05 confirmed that the stock non-Orange RC xApp and agent support the `Radio Bearer Control` / `QoS flow mapping configuration` operation, not scheduler configuration. Native LENA exposes runtime attributes such as `FixedMcsDl`, but no installed standard RC action maps to them. See [phase05-control-gap.md](/home/hpclab-lananh/HERO-slicing/integration/docs/phase05-control-gap.md) for source-linked findings.

## Phase 06 implemented private extension

For the Phase 06 targeted-control acceptance test, the native agent advertises a vendor-local RC function name and style `32767`, action `1` (`Set cell/BWP MaxDlMcs`), BWP parameter `1`, MCS parameter `2`, and outcome parameters `3`/`4`. These identifiers and semantics are integration-local; they are not claimed to be standardized. A matching local FlexRIC xApp discovers that exact definition, targets a specific gNB identity, and requests BWP 0 with an integer in `0..30`.

The handler rejects other header/message formats, multiple styles/actions, unknown parameter IDs, missing/duplicate/non-integer parameters, nonzero BWP indices, or out-of-range MCS values. It copies the request into a per-agent queue. An ns-3 event drains the queue and uses `NrHelper::GetScheduler` plus `SetAttributeFailSafe("MaxDlMcs", ...)` on the selected gNB's BWP 0. Only the E2AP agent for that gNB receives the request, and the event runs on the simulator thread. The result includes the applied flag and effective attribute value. Live validation changed gNB 102 to MCS cap 1 and observed its later DL grant MCS samples capped at 1 while the other gNB retained samples above 1.

Original FlexRIC's generic E2AP handler sets the transport-level control acknowledgement to success while returning a service-model outcome separately. The E2AP ACK alone is not proof of actuation: the xApp log, native application log, and post-control scheduler trace are all checked. This mechanism is private and must not be described as interoperability with arbitrary standard RC xApps.
