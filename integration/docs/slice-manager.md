# LENA slice-budget extension (experimental)

The integration now includes explicit QoS-flow/UE slice labels, native bearer configuration, slice-aware CSV metadata, local/regional PPO, a per-gNB `hero::slicing::SliceBudgetManager`, and an opt-in scheduler derived from native `NrMacSchedulerOfdmaPF`.

## Runtime path

`native-measurements` accepts `--enableSliceEnforcement=1` only with `--sliceAssignment=explicit|random`. Before installing devices it selects `NrMacSchedulerHeroSlicePF` through the verified `NrHelper::SetSchedulerTypeId` API. Each gNB gets its own manager. The gNB's native RRC `ConnectionEstablished` trace maps `(IMSI, cell, RNTI)` to the already-configured UE slice; no global cross-gNB map is used. The scheduler keeps the inherited PF UE selection, then trims a slice's allocation to its upper budget for that scheduling invocation. Downlink and uplink are enforced separately.

Budget units are RBG-symbol opportunities. The per-symbol limit follows the design's `floor(alpha_s * N_RBG)`; for `symAvail` symbols the maximum is `floor(alpha_s * N_RBG) * symAvail`. This avoids representing a bandwidth share as a fixed BWP or changing carrier/BWP configuration. `N_RBG` is the scheduler's configured RBG count, not raw PRBs. Whole-symbol grants mean actual usage can be below the cap. Unused capacity is not redistributed to another slice in this first extension. This is strict upper-cap enforcement, not a minimum guarantee or a new QoS scheduler.

The manager validates a finite, nonnegative three-ratio simplex and supports `SetRatios(ratios, policyVersion, source)`. All manager operations are simulation-thread-only. With `--enablePpo=1`, `PpoActionClient` requests an action from the local PPO sidecar after a complete measurement window and calls this API from the provider's ns-3 event callback. It never calls LENA state from the Python process. RNTIs that have no configured slice entry are left on the inherited PF policy and are not counted against a slice budget; the scenario maps every configured UE and prints successful RRC mapping logs. Handover migration/release handling is not yet included, so this experimental mode is for the current static-attachment scenarios.

## PPO relationship and boundaries

The live PPO action/reward loop is now integrated and tested for one-gNB local and two-gNB regional operation. Local mode owns one policy per gNB; regional mode produces one distinct action per gNB from an aligned multi-cell state. The selected mode is exclusive, so there is no simultaneous-tier arbitration. Actual transitions are written to JSONL and policy checkpoints are saved after training updates. Inference loads a compatible checkpoint and applies deterministic ratios. The missing ConvexSolve objective/constraints are not invented: only the ratio simplex is validated before manager application.

The learning contract remains experimental. Reward coefficients and regional arithmetic-mean aggregation are engineering choices, and the documentation does not claim they are the final paper formulation. The sidecar currently runs synchronously from the event thread and can delay real-time E2 scenarios. E2 RC remains the existing private `MaxDlMcs` extension; no slice-ratio E2 operation is advertised or inferred.

## Reproduce the enforcement check

```bash
cmake -S integration -B integration/build
cmake --build integration/build --target native-measurements slice-budget-manager-test -j2
ctest --test-dir integration/build -R 'slice-budget-manager-unit|native-slice-budget-enforcement|native-live-ppo-budget' --output-on-failure
integration/build/native-measurements --gnbs=1 --ues=6 --duration=2 --sampleMs=100 \
  --csv=/tmp/hero-slice-enforcement.csv --sliceAssignment=explicit \
  --embbUes=2 --urllcUes=2 --mmtcUes=2 --enableSliceEnforcement=1 \
  --embbRatio=0.5 --urllcRatio=0.3 --mmtcRatio=0.2 --enableE2=0 --realtime=0
```

The scheduler asserts after each enforcement pass that registered slice allocations do not exceed their per-slot budget. A successful native test also requires real bidirectional UDP delivery and nonzero LENA measurement windows. The upstream `NrMacSchedulerOfdmaPF` and default scenario remain unchanged when the flag is absent or false.

For live PPO, use `--enablePpo=1 --ppoTier=local|regional --ppoMode=train|infer`. See [PPO learning](ppo-learning.md) for checkpoints, reward fields, JSONL transitions, dashboard use, and live scenario commands.

## Design limitation

The supplied HERO design depicts slice budgets as ratios over RBGs and leaves per-UE scheduling to LENA. This extension enforces an upper cap after the existing PF selection; it does not guarantee a slice's minimum, meet latency/reliability constraints, or solve the paper's unprovided feasibility/rounding optimization. It also does not implement dynamic bandwidth-part reassignment. Live local or regional PPO now supplies event-aligned ratio actions from completed native measurement windows; action application and limitations are documented in [PPO learning](ppo-learning.md). Remaining work includes an authoritative paper-defined reward and feasibility/rounding contract, explicit minimum guarantees or budget borrowing, dynamic BWP reassignment, and any E2-based slice-control operation. Those behaviors are not implied by the current implementation.
