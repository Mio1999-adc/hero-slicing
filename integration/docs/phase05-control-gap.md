# Phase 05 — RC control compatibility result

## Result

The installed original FlexRIC and native LENA versions do not provide a shared, standard E2SM-RC operation for changing a LENA scheduler parameter. Phase 05's standard-control acceptance criteria cannot be met safely with the installed service model. No control action is advertised or sent, and no scheduler state is changed by this phase.

This result follows the project constraint against presenting an unsupported operation as standard E2SM-RC. FlexRIC's generic ASN.1 containers can represent arbitrary parameter data, but their existence is only codec capability; it does not define a standardized scheduler control style or parameter.

## Inspected FlexRIC RC capability

The installed revision is `f56d4487507c7ace392f1ec3f598e7eeda670acc`. Its RC service model is `SM_RC_ID=3`, `SM_RC_REV=1`, OID `1.3.6.1.4.1.53148.1.1.2.3` in [rc_sm_id.h](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/sm/rc_sm/rc_sm_id.h). The agent implementation in [rc_sm_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/sm/rc_sm/rc_sm_agent.c) decodes a control header and message, invokes the synchronous `write_ctrl` service-model callback, and encodes the returned control outcome. The generic E2AP agent dispatches `RIC_CONTROL_REQUEST` through [msg_handler_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/msg_handler_agent.c).

The ASN.1 RC v1.03 definitions and codec expose Control Header formats 1/2, Control Message formats 1/2, and Control Outcome formats 1/2/3. These are wire formats, not supported operation definitions. The installed non-Orange [xapp_kpm_rc.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/examples/xApp/c/kpm_rc/xapp_kpm_rc.c) accepts only the advertised `Radio Bearer Control` style and the `QoS flow mapping configuration` action (action ID 2). It sends DRB ID and QoS-flow mapping parameters. FlexRIC's [RC service-model README](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/sm/rc_sm/README.md) likewise describes that xApp as creating a DRB. Neither source defines a scheduler-parameter action or maps this bearer operation to a scheduler attribute.

The E2AP v1 agent handler in [msg_handler_agent.c](/home/hpclab-lananh/HERO-slicing/platforms/flexric/src/agent/msg_handler_agent.c) asserts that the RIC requested an acknowledgement and always sets E2AP Control Acknowledge status to `RIC_CONTROL_STATUS_SUCCESS`. It carries an optional encoded service-model control outcome, but this path is not a general E2AP failure response mechanism. Any future handler must validate malformed/unsupported requests before they reach assert-based generic code and must distinguish service-model execution outcome from E2AP procedure status.

## Native LENA mapping candidate (not implemented)

Native ns-3 is revision `ab4cce021d8f6b2458784704a10af810d3969f0f`. The actual scheduler TypeId in [nr-mac-scheduler-ns3.cc](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/model/nr-mac-scheduler-ns3.cc) exposes `FixedMcsDl` (boolean) and `StartingMcsDl` (unsigned integer) with attribute accessors. `NrHelper::GetScheduler(device,bwpIndex)` in [nr-helper.cc](/home/hpclab-lananh/HERO-slicing/platforms/ns-3/contrib/nr/helper/nr-helper.cc) resolves a native gNB BWP's scheduler. These attributes can be addressed through ns-3's `Object::SetAttribute` interface, but no supported RC v1.03 action in the installed FlexRIC identifies either parameter or defines its control semantics, bounds, or result representation.

| Candidate operation | LENA target | Input/validation | Runtime effect | Phase 05 status |
|---|---|---|---|---|
| Fix/unfix DL MCS | Selected gNB BWP scheduler, attribute `FixedMcsDl` | Boolean; constrain target gNB and BWP; verify target derives from `NrMacSchedulerNs3` | When enabled, scheduler uses `StartingMcsDl` as its fixed DL MCS | LENA API exists; no matching standard FlexRIC RC action |
| Set starting DL MCS | Selected gNB BWP scheduler, attribute `StartingMcsDl` | Unsigned integer validated against the scheduler's supported MCS range | Changes the configured fixed/start MCS used by the scheduler | LENA API exists; no matching standard FlexRIC RC action |
| QoS flow mapping configuration (FlexRIC stock RC action) | Bearer/QoS context, not a scheduler parameter | Stock xApp sends DRB ID and QoS-flow mapping data | Defines a bearer mapping operation; no installed adapter mapping it to scheduler configuration | Supported by FlexRIC codec/example, outside this phase's scheduler-control objective |

## Required safe extension proposal

If scheduler control remains a requirement, define and review an explicitly private integration extension before implementation. It must not be advertised as a standardized E2SM-RC style. The proposal is a separately named vendor-local control contract for one gNB/BWP scheduler operation, initially `FixedMcsDl` only, with:

1. A documented extension identifier/version and unambiguous control style, action, and parameter names; the xApp must discover and validate that exact definition before sending.
2. A bounded, typed input and explicit success/rejection result. The handler must reject unknown IDs, unsupported scheduler types, invalid values, stale targets, and requests after simulation shutdown.
3. Targeting by the E2 node identity and BWP, with any UE identity rejected because this setting is cell/BWP-level.
4. A simulation event bridge that copies request values into owned storage and applies `SetAttribute` only on the ns-3 event thread. Since FlexRIC invokes `write_ctrl` synchronously and immediately encodes its returned outcome, a control must not be acknowledged as applied until simulation-thread execution completes; timeout/shutdown must return a non-success service-model outcome and log that no change occurred.
5. End-to-end testing with an original FlexRIC xApp plus a before/after scheduler trace or native measurement that demonstrates the selected attribute's effect.

This private extension is a proposal only. It was not implemented because the requested standard RC mapping is absent and extending the protocol contract requires a separate, explicit product decision.

## Validation performed

Source inspection only for this phase. The existing Phase 04 tree was then rebuilt and its focused CTest suite run to ensure this compatibility finding did not alter the working measurement and KPM path. No E2 control request was sent and no RC control xApp run is claimed.
