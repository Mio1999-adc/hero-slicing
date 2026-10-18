# Independent O-RAN integration — Phase 06

The independent `integration/` platform connects multiple native 5G-LENA gNBs to original FlexRIC over SCTP/E2AP. It reports real per-node KPM v3 DL/UL PDCP SDU throughput and includes a clearly private RC extension for a tested per-gNB `MaxDlMcs` change. The end-to-end multi-gNB KPM/control run is verified with two gNBs. Three-gNB radio operation and E2 Setup are verified, but the installed stock monitoring xApp aborted during three concurrent subscriptions; see [Phase 06 validation](docs/phase06-validation.md). The private control is not a standard E2SM-RC scheduler operation.

The local System Configuration Manager provides profile forms and process control for verified native scenarios and FlexRIC processes. Start it with `./integration/scripts/start-dashboard.sh` and open `http://127.0.0.1:8090`; it binds to loopback by default. See [Phase 08 configuration manager](docs/phase08-configuration-manager.md) for supported controls, startup/runtime boundaries, and validation evidence.

Configure/build and run the focused tests from the repository root:

```bash
cmake -S integration -B integration/build
cmake --build integration/build -j2
ctest --test-dir integration/build --output-on-failure
```

Run the longer upstream and end-to-end validation suite with `./integration/scripts/run-phase07-validation.sh`.

Run the full loopback integration against the original RIC and stock monitoring xApp:

```bash
python3 -u integration/tests/run-phase04.py --duration=4
```

Run the full two-gNB Phase 06 check, including the stock KPM monitor, targeted private-control xApp, exact APER message sizes, and resource sampling:

```bash
python3 -u integration/tests/run-phase06.py --duration=8 --gnbs=2 --ues-per-gnb=2
python3 -u integration/tests/run-phase06-isolation.py
```

It requires available loopback SCTP ports 36421 and 36422, starts only the processes it owns, captures the SCTP association, RIC/xApp/native logs and CSV under `integration/build/phase04-validation/`, and exercises subscription deletion before gNB shutdown. The stock RIC fixes its E2 port at 36421. The default integration build imports the existing ns-3.42 debug build and original FlexRIC `E2AP_V1`/`KPM_V3_00` ASN build without reconfiguring upstream trees.

## Faster radio-only dashboard build

The original ns-3 debug build is preserved. For faster radio-only simulations, build an optimized ns-3 profile alongside it, then link a separate integration build to those libraries. The first command creates `platforms/ns-3/cmake-cache-optimized/` and `platforms/ns-3/build-optimized/`; it does not overwrite or install over the existing debug build.

```bash
cmake -S platforms/ns-3 -B platforms/ns-3/cmake-cache-optimized \
  -DCMAKE_BUILD_TYPE=release \
  -DNS3_OUTPUT_DIRECTORY="$PWD/platforms/ns-3/build-optimized" \
  -DNS3_NATIVE_OPTIMIZATIONS=ON -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_EIGEN=ON -DNS3_SQLITE=ON -DNS3_CCACHE=OFF
cmake --build platforms/ns-3/cmake-cache-optimized \
  --target libnr libinternet libapplications libpoint-to-point -j8
HERO_INTEGRATION_BUILD_DIR="$PWD/integration/build-optimized" \
HERO_NS3_BUILD_DIR="$PWD/platforms/ns-3/build-optimized" \
HERO_NS3_BUILD_PROFILE=optimized HERO_BUILD_JOBS=4 \
  ./integration/scripts/build-integration.sh
HERO_INTEGRATION_BUILD_DIR="$PWD/integration/build-optimized" \
  ./integration/scripts/start-dashboard.sh
```

The regular `./integration/scripts/start-dashboard.sh` command automatically selects `integration/build-optimized/` when that build exists, and otherwise falls back to the original `integration/build/` debug build. Set `HERO_INTEGRATION_BUILD_DIR` explicitly to choose another build. E2/SCTP scenarios remain real-time paced and retain the existing FlexRIC compatibility limits. Radio-only scenarios run unpaced at the maximum speed available to the selected ns-3 build. Continuous dashboard runs use staggered bursty UDP traffic; finite regression-test workloads remain unchanged. For more responsive local charts, select a 100 ms sampling interval and leave the stock KPM monitor off (it requires 1000 ms reports).

## Experimental LENA slice budgets and PPO

Native UE slice assignment remains optional. `--enableSliceEnforcement=1` selects `NrMacSchedulerHeroSlicePF`, derived from the installed OFDMA PF scheduler. With `--enablePpo=1`, live local or regional PPO actions replace the manager ratios after each complete native measurement window; without PPO, startup ratios remain fixed. The original LENA PF scheduler remains the default baseline. See [slice manager and enforcement](docs/slice-manager.md) and [live PPO slice-budget control](docs/ppo-learning.md).

Run `ctest --test-dir integration/build -R 'slice-budget|multignb-slice-budget|native-live-ppo-budget' --output-on-failure` for manager, static one-/two-gNB enforcement, and live local/regional PPO scenarios. The PPO check uses actual LENA traffic, verifies applied per-gNB policy ratios, and checks transition/checkpoint files.

The dashboard now exposes PPO tier, train/infer mode, update interval, seed, and experimental reward coefficients. It rejects inference unless matching checkpoints exist. PPO uses local simulator IPC and applies through the simulation-thread manager API; it is not a slice-control E2 operation. The synchronous sidecar can slow real-time runs, so radio-only unpaced execution is the validated training path. Reward details are experimental and are not claimed as finalized HERO paper equations.

For the component diagram, module ownership, differences from the original LENA/FlexRIC path, and current learning status, see [HERO-Slicing learning architecture](docs/hero-learning-architecture.md).

## PPO policy implementation

The local/regional PPO controller uses the installed PyTorch runtime and actual native measurement windows. It can train or load checkpoints and apply per-gNB ratios through the simulation-thread budget manager. The policy primitives and offline state CLI use the same CSV schema. Build/configure the integration once to register the unit test, then run:

```bash
cmake -S integration -B integration/build
ctest --test-dir integration/build -R ppo-learning-unit --output-on-failure
python3 -m integration.learning.state_cli --tier local \
  --csv integration/build/dashboard-runs/<run>/measurements.csv \
  --output /tmp/hero-local-observations.jsonl
python3 -m integration.learning.state_cli --tier regional --max-gnbs 3 \
  --csv integration/build/dashboard-runs/<run>/measurements.csv \
  --output /tmp/hero-regional-observations.jsonl
```

`integration/docs/ppo-learning.md` documents observation provenance, live action alignment, transition logs, checkpoints, experimental reward details, runtime commands, and remaining methodology limits. The local/regional PPO controller and simulation-event integration live alongside the policy primitives under `integration/learning/`.

Read [architecture](docs/architecture.md), [E2 protocol workflow](docs/e2-protocol-workflow.md), [multi-gNB architecture](docs/multi-gnb-architecture.md), [measurement mapping](docs/measurement-mapping.md), [control capabilities](docs/control-capabilities.md), [installation and execution](docs/installation-execution.md), [troubleshooting](docs/troubleshooting.md), and [known limitations](docs/known-limitations.md). Validation reports are [Phase 04](docs/phase04-validation.md), [Phase 06](docs/phase06-validation.md), and [Phase 07 final validation](docs/phase07-final-validation.md); historical reports are [Phase 01](docs/validation.md), [Phase 02](docs/phase02-validation.md), [Phase 03](docs/phase03-validation.md), and [Phase 05 control compatibility](docs/phase05-control-gap.md).
