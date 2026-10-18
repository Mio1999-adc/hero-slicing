# UE-aware local PPO formulation

This document specifies the opt-in `ue-aware` formulation for independent gNB-local PPO. It extends the existing local action path and leaves FlexRIC, E2, regional PPO, the native UE-level PF policy, and the legacy PPO formulation intact. The native CLI keeps `baseline` as its compatibility default; the dashboard selects UE-aware for new local configurations. Select `baseline` explicitly for old checkpoint comparison. A regional run must use `baseline`.

## Audit of the prior local pipeline

The previous path was already connected end to end: `LenaMeasurementProvider` sampled native PHY/MAC/PDCP traces; `MeasurementCsvWriter` wrote per-cell and per-UE rows; `RuntimeController` built a fixed 31-feature cell observation plus availability masks; one `LocalPpoAgent` per gNB sampled a three-value Dirichlet simplex; the native event callback applied each vector to that gNB's `SliceBudgetManager`; the PF-derived scheduler retained its normal UE selection and trimmed allocations above each slice's RBG-symbol cap; the next completed window supplied the reward and next state. Every local agent has its own model, rollout, seed offset, policy version, and checkpoint.

The baseline uses fixed per-feature engineering scales, a configurable throughput/delay/SLA reward, and a ratio-change penalty. It does not include per-slice QoS aggregates or action-aligned scheduler budget/use in its observation. The provider does not expose queue backlog, packet loss, BLER, or HARQ counters. The native scenario uses one shared BWP and per-slice scheduler caps, not dedicated BWP bandwidth partitions. The PPO bridge runs synchronously on the ns-3 event thread: this preserves safe application of actions but makes Python training wall time pause simulation progress.

Relevant implementation files are `integration/learning/state.py`, `hero_ppo.py`, `tiers.py`, `runtime_controller.py`, `ppo-action-client.{h,cc}`, `integration/slicing/slice-budget-manager.{h,cc}`, `nr-mac-scheduler-hero-slice-pf.{h,cc}`, `integration/kpm/lena-measurement-provider.cc`, `measurement-csv.{h,cc}`, and `integration/examples/native-measurements.cc`.

## UE-aware observation

The observation has **144 values**: 24 fixed features for each of three slices, each represented as `(normalized value, availability bit)`. Its dimension does not depend on the number of connected UEs. Slices are ordered eMBB, URLLC, mMTC. Features, in order per slice, are:

1. Connected UE count, offered bit rate, required/proxy bit rate.
2. Estimated aggregate, mean, and maximum UE resource demand; demand-to-observed-capacity ratio; demand dispersion.
3. Measured DL and UL delivered PDCP-SDU throughput; mean DL PDCP delay; mean per-UE DL SINR, CQI, and MCS.
4. Previous applied ratio, actual slice budget share, actual MAC-use share, and unused budget share.
5. Mean UE QoS satisfaction, worst UE satisfaction, satisfied-UE fraction, throughput deficit, delay violation, and packet-loss violation.

SINR is derived from the provider's positive linear SINR samples. PDCP throughput and delay, CQI, MCS, per-UE DCI grants, and PDCP delivered bytes come from native rows. Ratios and shares are bounded; nonnegative magnitudes use a finite log transform; signed SINR is scaled to `[-1,1]`; missing values carry a zero value with mask `0`. The exact normalized state, feature names, and source diagnostics are persisted in each actual transition JSONL record.

### UE resource demand

For a completed window of duration `Δt`, the configured required throughput is used when positive; otherwise configured offered rate is used as an explicit proxy. Per UE, delivered PDCP SDU bits divided by measured allocated DL PRB-symbols converted to RBG-symbols (`PRBs / PRBs-per-RBG`) estimates `η` in bits per RBG-symbol. Demand is `Vreq / max(η, ε)`, where `Vreq = rate × Δt`. Per-slice demand is summed; dispersion is the coefficient of variation of valid UE estimates. The observed capacity denominator is the sum of the actual DL slice budgets recorded by the manager during the window, also in RBG-symbol opportunities.

This is a measured service-efficiency estimator, not a queue-aware predictor. Urgent queued bytes and deadline-specific queued traffic are unavailable. If a connected UE has positive configured demand but no measured delivered service or allocation, that UE's demand is unavailable for that window; the record preserves this as `null` and the demand feature mask is zero. Reward adequacy omits slices without a complete demand estimate and records its eligible slice list. This avoids substituting a synthetic efficiency or queue value.

`SliceBudgetManager::TakeWindowStats()` snapshots and resets the per-gNB counters. The derived scheduler records its actual per-direction RBG-symbol budget and use on each scheduling invocation, including empty active-UE maps when invoked. The cell CSV row records the ratio active during the completed window plus DL/UL used and budget counters by slice. UE rows leave these cell-scope fields empty. The manager's counters and the measurement event are read on the ns-3 owning thread.

## UE QoS and bounded reward

UE satisfaction uses the requested bounded throughput and delay violation equations. Slice-specific experimental QoS weights are eMBB `(α,β,γ)=(0.7,0.3,0)`, URLLC `(0.3,0.7,0)`, and mMTC `(0.8,0.2,0)`. When a configured target or sample is unavailable, the available nonzero dimensions are renormalized for that UE. Packet-loss telemetry is unavailable in the native provider; `γ=0`, the state packet-loss feature is unavailable, and the reward never pretends a loss measurement exists. The three QoS weight sets sum to one but are initial experimental choices, not paper-derived constants.

Components are:

- `Q`: mean bounded satisfaction over connected UEs with at least one available configured QoS target.
- `A`: active-UE-count-weighted slice budget adequacy `1 - min(1, max(D-B,0)/D)`. A zero-demand slice has zero deficit. Slices with unavailable demand or budget are excluded and listed in diagnostics; if none are eligible, `A=0` with those missing inputs recorded.
- `E`: useful resource-equivalent service divided by observed total DL budget capacity. For each UE, actual allocated RBG-symbols are multiplied by `min(1, delivered PDCP SDU bits / scheduled MAC TB bits)`, then capped by estimated UE demand. This is a bounded delivered-service proxy; scheduled PRBs alone are not treated as useful delivery.
- `F`: Jain fairness over UE satisfaction, normalized to `[0,1]`; a single UE yields `1`, and all-zero satisfaction with multiple UEs yields `0`.
- `H`: half the L1 distance between the manager's actual applied ratios for the outcome window and the previously applied ratios.

The final reward is `r = 0.50 Q + 0.20 A + 0.15 E + 0.10 F - 0.05 H`. The dashboard and CLI expose all five weights; they must be nonnegative and sum to one. Every component is validated in `[0,1]`. The implementation checks and records both guaranteed bounds `[-wH, 1-wH]` and state-action bounds `[wA A - wH H, wA A - wH H + wQ + wE + wF]`. At the initial weights these are `[-0.05, 0.95]` globally. A bound violation raises an error; the result is not clipped.

## PPO, action alignment, and checkpoints

The actor remains a Dirichlet policy over the three-slice simplex. The same float32 sampled action is used for its stored log probability and later PPO evaluation. No post-sampling projection is applied: `SliceBudgetManager` accepts the same simplex ratios and converts them to per-slot floor RBG-symbol caps. The leftover units caused by integer flooring remain unused by existing scheduler behavior.

The online order remains: build the current state, sample an action, apply it to that gNB, collect the next completed measurement window, calculate the reward for the previous action, store `(s,a,logπ,r,s')`, and update only after the configured rollout count. Local controllers process gNB requests independently and sequentially on the simulation thread; there is no shared local rollout or cross-gNB training. Existing GAE, advantage normalization, PPO clipping, minibatch shuffling, gradient clipping (default norm `0.5`), entropy regularization, and approximate-KL reporting remain. `target_kl` can be enabled in `PpoConfig` for early stop; it is currently unset by default. Baseline PPO defaults remain unchanged.

UE-aware local checkpoints are named `local-ue-aware-gnb-<id>.pt`; baseline checkpoints retain `local-gnb-<id>.pt`. Observation dimension checks prevent cross-loading. The dashboard's inference validation checks for the checkpoint set matching both selected gNB count and formulation.

## Training diagnostics and current evidence

The Live PPO pane now exposes actual reward and PPO update diagnostics, UE-aware Q/A/E/F/H and analytical bounds, plus per-gNB applied slice ratios and measured budget utilization. The transition journal retains per-slice estimated demand, satisfaction, budget/use values, state vector, availability, and missingness. Update records include actor/critic loss, entropy, approximate KL, gradient norm, update duration, and policy version.

Validation currently establishes implementation correctness, not improved convergence. A short real one-gNB run produced 13 action-aligned transitions and 6 finite PPO updates, with all recorded rewards/components inside their bounds. A separate matched-seed comparison ran the legacy baseline and UE-aware local PPO at seeds 11, 23, and 37, using the same one-gNB/three-UE scenario, one UE per slice, six simulated seconds, and 200 ms measurement windows. Each formulation produced 28 transitions and three updates per seed.

| Common external metric | Baseline mean ± sample SD | UE-aware mean ± sample SD |
|---|---:|---:|
| DL PDCP SDU throughput (Mbit/s) | 0.9744 ± 0.0921 | 1.0245 ± 0.0275 |
| Mean UE throughput-target satisfaction | 0.3866 ± 0.0064 | 0.3780 ± 0.0057 |
| Mean DL PDCP delay (ms) | 15.85 ± 6.53 | 12.20 ± 3.21 |
| Wall-clock run time (s) | 4.940 ± 0.059 | 4.979 ± 0.084 |

In this short scenario, UE-aware runs had higher mean throughput and lower mean delay, but lower mean UE throughput-target satisfaction. Three seeds and three updates per seed are insufficient to claim faster convergence or general performance improvement. These numbers are an exploratory comparison, not a tuned hyperparameter study, and the satisfaction metric is not the same as the PPO reward. Raw per-seed output, window CSV, and plot are [summary JSON](ppo-local-convergence-short.json), [window metrics CSV](ppo-local-convergence-short.csv), and [matched-seed plot](figures/ppo-local-common-metrics-short.png).

Reproduce with a fresh output directory:

```bash
python3 integration/tests/run-ppo-convergence-study.py \
  --binary integration/build-optimized/native-measurements \
  --output integration/build-optimized/ppo-convergence-study \
  --seeds 11 23 37 --duration 6
```

Run the UE-aware local experiment after building the integration target:

```bash
cmake -S integration -B integration/build-optimized -DNS3_BUILD_PROFILE=optimized
cmake --build integration/build-optimized --target native-measurements -j2
integration/build-optimized/native-measurements \
  --gnbs=1 --ues=3 --duration=8 --sampleMs=200 \
  --sliceAssignment=explicit --embbUes=1 --urllcUes=1 --mmtcUes=1 \
  --enableSliceEnforcement=1 --enablePpo=1 --ppoTier=local --ppoFormulation=ue-aware \
  --ppoMode=train --ppoUpdateEvery=8 --ppoSeed=1 \
  --ppoCheckpointDir=integration/build-optimized/ppo-checkpoints/local \
  --csv=integration/build-optimized/ppo-ue-aware.csv --enableE2=0 --realtime=0
```

The code builds against the existing local integration tree; do not reconfigure or reinstall native ns-3, LENA, or FlexRIC for these changes. Run focused tests with:

```bash
python3 -m unittest integration.tests.test_learning integration.tests.test_runtime_controller_ipc
ctest --test-dir integration/build-optimized -R 'slice-budget-manager-unit|native-live-ppo-budget|dashboard-config-validation|dashboard-server-behavior' --output-on-failure
```
