# Slice-associated UE traffic and QoS profile path

This page documents the optional UE traffic and QoS-profile path in the native LENA demo. It preserves the installed BWP and the original MAC scheduling policy. The scenario now has a separate, opt-in slice-budget enforcement path: `SliceBudgetManager` applies per-gNB ratios through a derived PF scheduler, and live PPO can update those ratios from native measurement windows. These are experimental local simulator controls; they do not alter the original LENA baseline, implement E2 slice-ratio control, or claim the final HERO paper objective. See [slice manager](slice-manager.md) and [PPO learning](ppo-learning.md).

## Mapping and bearer behavior

Slice identity is local simulator metadata, independent of QCI/5QI:

| Slice ID | Name | Application | Native dedicated EPS bearer | Default traffic |
|---|---|---|---|---|
| 1 | eMBB | video | `NGBR_VIDEO_TCP_DEFAULT` (QCI 9) | 1200-byte payload, 10 Mbit/s offered per direction |
| 2 | URLLC | industrial control | `DGBR_DISCRETE_AUT_SMALL` (QCI 82) | 128-byte payload every 10 ms |
| 3 | mMTC | sensor reporting | `NGBR_MC_DATA` (QCI 70) | 64-byte payload every 1000 ms |

The traffic generator adds its native `SeqTsHeader` to the configured payload size. Per-UE TFT packet filters use the UE's DL sink port and UL server port, so each dedicated bearer classifies only that UE's two configured UDP flows. This keeps a single primary slice per UE for now and leaves a future per-flow identity extension possible. The dedicated QCI selects supported bearer treatment; it does not guarantee the configured application SLA.

`--sliceAssignment=off` is the default and retains the baseline 512-byte DL / 256-byte UL packets with 5 ms / 7 ms intervals. `explicit` requires the three UE counts to sum to `--ues`. `random` uses an ns-3 `UniformRandomVariable` stream and requires nonnegative probabilities that sum to 1. Seed and run values are logged and forwarded to `RngSeedManager`; random assignment produces a realized count that need not match its expected proportions.

Example three-slice run:

```sh
integration/build/native-measurements \
  --gnbs=2 --ues=9 --gnbSpacingM=500 --ueDistanceM=20 \
  --sliceAssignment=explicit --embbUes=3 --urllcUes=3 --mmtcUes=3 \
  --embbPacketBytes=1200 --embbRateBps=10000000 --embbRequiredBps=10000000 \
  --urllcPacketBytes=128 --urllcIntervalUs=10000 --urllcDelayTargetMs=10 \
  --urllcReliabilityTarget=0.9999 --mmtcPacketBytes=64 --mmtcIntervalUs=1000000 \
  --mmtcDelayTargetMs=1000 --mmtcReliabilityTarget=0.99 \
  --seed=7 --rngRun=2 --duration=2 --sampleMs=100 \
  --enableE2=0 --realtime=0 --csv=/tmp/hero-three-slice.csv
```

Example seeded random assignment:

```sh
integration/build/native-measurements \
  --gnbs=1 --ues=30 --sliceAssignment=random \
  --embbProbability=0.4 --urllcProbability=0.3 --mmtcProbability=0.3 \
  --seed=77 --rngRun=4 --duration=8 --sampleMs=100 \
  --enableE2=0 --realtime=0 --csv=/tmp/hero-random-slices.csv
```

The dashboard offers the same startup fields under “UE slices and traffic”, saves them in JSON profiles, and forwards them as executable arguments. `/api/slices` reads only complete records from the active run CSV and returns realized per-UE/per-gNB mapping. A configured minimum DL throughput is compared with delivered DL PDCP SDU throughput; the displayed ratio is `min(1, observed / configured minimum)`. This is a local diagnostic and does not replace any HERO reward or paper SLA definition. UEs without a configured throughput target, and configured delay/reliability dimensions without compatible measurements, are shown as `INSUFFICIENT_DATA`.

## Telemetry definitions and boundaries

| Value | Source and unit | Window / aggregation | Scope and limitation |
|---|---|---|---|
| Slice ID/type, application, QoS profile | Scenario assignment and `ProfileFor`; ID and strings | Fixed for the run; copied into provider UE context | UE, local CSV/dashboard metadata; not an E2 KPM label |
| Offered rate | Configured payload bits / app packet interval, bit/s | Constant configured rate per direction | UE; excludes protocol headers and is not achieved throughput |
| Achieved DL throughput | Native gNB/UE PDCP receive trace; delivered SDU bytes × 8 / sample seconds, Mbit/s | Provider sample interval | UE/cell; successfully received at PDCP, not application goodput |
| DL PRB grants | Existing DCI trace grant sum | Sum over measurement window | UE/cell; native usage, not a slice budget |
| DL PRB-symbols | Existing DCI trace `RBG × symbol` sum converted by provider to PRB-symbols | Sum over measurement window | UE/cell; current one-BWP context |
| Bits/PRB-symbol | DL received PDCP SDU bits / DL PRB-symbol sum | Same measurement window | UE; diagnostic efficiency, not a universal SLA threshold |
| Delay target/reliability target | Scenario configuration | Fixed for run | UE metadata only; no application-level verdict is derived |

Actual application deadline misses, packet delivery ratio, and queue backlog by QoS flow remain unavailable. Per-gNB `SliceBudgetManager`, opt-in PF-derived slice caps, and live PPO ratio changes are implemented separately; current budgets and their source/version are emitted in native simulation logs, while the CSV still carries UE slice metadata. The existing FlexRIC E2/KPM/control path is unchanged and does not carry PPO slice actions. See [slice manager](slice-manager.md), [PPO learning](ppo-learning.md), [the historical audit](hero-slicing-audit.md), and [known limitations](known-limitations.md) for the boundaries.
