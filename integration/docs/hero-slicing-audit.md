# HERO slice-aware simulation audit

Audit date: 2026-10-16. This is a source audit of the current workspace before any scheduler or HERO-policy changes.

**Historical snapshot:** The findings in the audit tables and the “Required source contract” section describe the implementation state when the initial audit was written. The later “Incremental implementation status” and “Standalone PPO extension status” sections record additions made afterward. For current behavior and validation, use [slice manager](slice-manager.md), [PPO learning](ppo-learning.md), and [HERO-Slicing learning architecture](hero-learning-architecture.md).

## Repository and version state

- The root Git index contains only `.gitignore` and `README.md`; there is no tracked HERO controller, learning implementation, paper-derived configuration, or test suite in this checkout. The current root status is `README.md` modified and `integration/` untracked. The integration files and local changes are preserved.
- Native ns-3/NR source is `platforms/ns-3`, Git revision `ab4cce021` (`Update VERSION and documentation tags for ns-3.42 release`). Tracked `contrib/nr` source has no local modifications. The untracked `platforms/ns-3/build-optimized/` and `platforms/ns-3/default` artifacts were left untouched.
- Original FlexRIC source is `platforms/flexric`, revision `f56d4487`. Existing untracked `.bak` files under FlexRIC were left untouched.
- Current native integration builds under `integration/build` and `integration/build-optimized`; this audit made no changes to ns-3, LENA, or FlexRIC source.

## Component audit

| Component | Current implementation and source | Classification |
|---|---|---|
| UE and gNB construction | `integration/examples/native-measurements.cc`: creates native `NrGnbNetDevice` and `NrUeNetDevice` through `NrHelper`, one 3.5 GHz / 20 MHz BWP, constant positions, `AttachToClosestGnb`, and up to 15 gNBs / 32 UEs. | Implemented and previously validated for native radio scenarios. |
| Traffic and QoS bearers | The example uses bidirectional UDP sources with common 512-byte DL / 256-byte UL packets and common 5 ms / 7 ms intervals. It does not select per-UE application profiles or explicitly configure slice-specific QoS bearers. Native `NrHelper::ActivateDataRadioBearer` and `ActivateDedicatedEpsBearer` exist in `platforms/ns-3/contrib/nr/helper/nr-helper.h`, but the scenario does not use them to define three slice profiles. | Basic traffic implemented; requested per-UE traffic/QoS profiles missing. |
| BWP and BWP Manager | One BWP is constructed in the native example. LENA's native BWP manager and bearer paths remain upstream and are not changed. No logical slice identity is mapped to a BWP or flow. | Native BWP operation present; logical slice mapping missing. |
| UE / flow slice association | No slice ID or slice-type field exists in `integration/common/interfaces.h`, `integration/kpm/native-measurements.h`, CSV schema, the scenario, or dashboard configuration. 5QI/QCI is not used as a slice identifier. | Missing. |
| gNB Slice Manager / budgets | No per-gNB slice manager, budget representation, budget feasibility validator, runtime update API, or resource-usage ledger is present. Existing E2 private control only targets `MaxDlMcs`; it is not a slice-ratio or budget action (`integration/control/private-rc-extension.h`, `integration/docs/control-capabilities.md`). | Missing. |
| MAC scheduling | Native `NrMacSchedulerNs3::AssignDLRBG` / `AssignULRBG` are allocation extension points in `platforms/ns-3/contrib/nr/model/nr-mac-scheduler-ns3.h`; native OFDMA assignment is in `nr-mac-scheduler-ofdma.cc`, and PF ordering/metrics are in `nr-mac-scheduler-ofdma-pf.{h,cc}`. `NrHelper::SetSchedulerTypeId`, `SetSchedulerAttribute`, and `GetScheduler` are available in `contrib/nr/helper/nr-helper.h`. The implementation is upstream and unmodified; it has no slice lookup or per-slice budget enforcement. | Baseline scheduler implemented; slice-aware scheduler missing. |
| Native measurements | `integration/kpm/lena-measurement-provider.{h,cc}` provides cell/per-UE native PDCP/RLC/MAC/PHY interval records. `integration/kpm/measurement-csv.{h,cc}` exports delivered PDCP bytes/packets, PDCP/RLC receive-delay trace aggregates, DCI-derived PRB grants and PRB-symbols, MCS, CQI, SINR and connection state. | Implemented and previously validated, with documented source/units. No slice labels. |
| Application QoS and satisfaction | The provider does not measure application offered/delivered bytes or end-to-end UDP packet deadlines/loss per UE. Existing PDCP receive delay is a radio-stack trace statistic and cannot be presented as end-to-end application latency. No configured per-UE minimum rate/reliability/deadline, satisfaction state, or bits/PRB-symbol report exists. | Missing; cannot truthfully compute the requested SLA dashboard yet. |
| E2 / KPM / control | Original FlexRIC integration is in `integration/e2-agent/` and `integration/kpm/`; implementation and limitations are recorded in phase validation docs. KPM wire output is cell DL/UL PDCP throughput only. Multi-gNB native radio scenarios exist; stock xApp compatibility remains limited as documented. | Existing integration; not a slice policy/control plane. |
| Dashboard/configuration | `integration/dashboard/server.py`, `index.html`, and JSON profiles configure gNB/UE counts, geometry, sampling, run mode, RIC/xApp flags and render the existing local measurement catalogue. No slice counts/probabilities, per-UE requirements, budgets, satisfaction table, or allocation hierarchy is connected to simulator output. | Existing dashboard; requested slice/QoS workflow missing. |

## Compatibility and implementation boundary

The installed scheduler offers a derived-scheduler extension path and UE-specific scheduling state. However, there is no HERO action contract/controller in the checked-in project to supply slice budgets, and no established slice IDs, QoS objective definitions, traffic-to-UE data model, or policy timing boundary. The prompt itself gives illustrative traffic profiles and an illustrative budget example, but explicitly forbids treating those examples as the operating algorithm or changing paper methodology.

Implementing an active slice-aware scheduler now would therefore require inventing at least the missing budget producer/action semantics and slice-to-UE association contract. A global notched-RBG mask is not a substitute: native LENA applies it to the BWP, not selectively by slice, and it would not establish the requested sum-of-allocations-per-slice invariant. Existing UE PRB-grant traces alone also cannot prove that a slice budget was enforced.

For the same reason, a green/red QoS status cannot be inferred from currently available counters: the existing provider has no configured requirements or application packet deadline/loss observations. PDCP throughput and delay are valid native telemetry but do not prove an end-to-end application SLA.

## Validation status

This audit did not change implementation code or run the prompt's ten new slice tests. Existing regression results remain in `phase03-validation.md`, `phase04-validation.md`, `phase06-validation.md`, and `phase07-final-validation.md`. They validate the existing native radio/E2/KPM/control integration, not slice assignment, slice-budget enforcement, dynamic budget changes, or QoS satisfaction. No new slice behavior is claimed as implemented or tested.

## Required source contract before implementing the missing policy path

The implementation can continue without changing HERO equations once the actual controller/action contract is available: slice IDs and mapping ownership; budget action representation and units; budget update/effective boundary; baseline UE scheduler policy; applicable per-slice SLA definitions; and authoritative per-UE traffic/QoS profile configuration. If the policy source is stored outside this checkout, add or point to that source and its versioned paper/configuration contract. Otherwise, explicitly authorize a new test-only budget input and specify which scheduler behavior should remain unchanged; that can validate the enforcement mechanism without claiming it is the HERO learning policy.

## Incremental implementation status (2026-10-16)

The native example and dashboard have since gained an opt-in UE traffic/profile path. `integration/examples/native-measurements.cc` now maps UEs to local IDs 1=eMBB, 2=URLLC, and 3=mMTC by explicit counts or seeded ns-3 random draws. It installs a dedicated native EPS bearer with per-UE port filters (QCI 9, 82, and 70 respectively), selects the configured UDP payload/interval, and keeps the old common traffic path when `sliceAssignment=off`. `UeContext` and the native CSV now carry slice type, application, bearer, offered rate, configured throughput target, packet settings, delay target, and reliability target. The dashboard passes validated profile values to the executable and renders per-UE slice/throughput rows and per-gNB counts.

The throughput status is a dashboard diagnostic against the configured minimum DL PDCP SDU throughput only. Delay/reliability targets are configuration metadata; the current provider does not measure end-to-end UDP latency or delivery ratio, so the dashboard does not declare them satisfied. `bits/PRB-symbol` is derived from successfully received DL PDCP SDU bits divided by the provider's DCI-derived DL PRB-symbol sum for the same sample. Existing KPM v3 contents are unchanged.

At the time of this audit, the slice-aware MAC scheduler, per-gNB budget manager, runtime budget action, and HERO controller integration were missing. These were subsequently added as an opt-in extension; current implementation and validation are documented in `slice-manager.md` and `ppo-learning.md`.

## Standalone PPO extension status

After this source audit, `integration/learning/` gained independent local and regional Dirichlet PPO controllers, fixed-order observation construction, live native-window transitions, and simulation-thread budget updates. Dashboard controls select the policy tier and train/infer mode. No ConvexSolve objective/constraints, E2 slice action, or simultaneous local/regional arbitration were invented; those remain outside the implemented extension. See the linked current-state documents for scope and validation details.
