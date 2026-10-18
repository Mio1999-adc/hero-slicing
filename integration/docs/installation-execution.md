# Installation and execution

## Requirements and installed source checks

This integration expects the repository's existing Ubuntu 24.04 native builds:

- ns-3.42 and 5G-LENA NR-v3.3 under `platforms/ns-3` (`contrib/nr/RELEASE_NOTES.md` identifies the NR release).
- Original FlexRIC commit `f56d4487507c7ace392f1ec3f598e7eeda670acc`, configured for `E2AP_V1`, `KPM_V3_00`, and ASN encoding.
- Linux SCTP support/library, CMake, a C/C++ toolchain, and Python 3.

From the repository root, verify the checked-out sources and FlexRIC build flags:

```sh
git -C platforms/ns-3 rev-parse HEAD
git -C platforms/flexric rev-parse HEAD
grep -E '^(E2AP_VERSION|KPM_VERSION|E2AP_ENCODING|SM_ENCODING_KPM|SM_ENCODING_RC):' platforms/flexric/build/CMakeCache.txt
```

The integration CMake project checks ns-3's `VERSION` file and the FlexRIC CMake cache. It imports the existing ns-3 build libraries and original FlexRIC archives/plugins; it does not reconfigure either upstream tree. If your source/build paths differ, pass `-DNS3_SOURCE_DIR=... -DNS3_BUILD_DIR=... -DFLEXRIC_SOURCE_DIR=... -DFLEXRIC_BUILD_DIR=...` to CMake.

## Build and automated tests

```sh
./integration/scripts/build-integration.sh
```

Equivalent commands:

```sh
cmake -S integration -B integration/build
cmake --build integration/build -j2
ctest --test-dir integration/build --output-on-failure
```

The CTest suite covers compilation/link compatibility, E2 identity round-trips, KPM subscription/action encoding and validation, private RC validation and ASN round-trip, radio-only mode, deterministic native measurement aggregation, and single/two-gNB scenarios. Live SCTP tests are separate because they bind local ports.

## Full automated validation

```sh
./integration/scripts/run-phase07-validation.sh
```

It builds/tests the integration, incrementally builds ns-3 core/NR and FlexRIC in their existing build trees, runs focused NR scheduler/notching tests and FlexRIC CTests, then invokes single-gNB E2 setup, single-gNB KPM, two-gNB KPM/control, and per-agent connection-failure tests. It requires loopback SCTP ports 36421, 36422, and 36423 to be free. Realtime E2 simulations are slower than simulated time; the two-gNB 8-second run took about 120 seconds on the validation host.

## Interactive final demonstration

Build first, then use separate terminals from the repository root.

Terminal 1 starts the original Near-RT RIC:

```sh
./integration/scripts/start-ric.sh
```

Terminal 2 starts the original FlexRIC KPM monitoring xApp:

```sh
./integration/scripts/start-kpm-monitor.sh
```

Terminal 3 starts native LENA with two gNBs and four UEs:

```sh
integration/build/native-measurements \
  --gnbs=2 --ues=4 --sampleMs=1000 --duration=12 \
  --csv=integration/build/demo-measurements.csv \
  --enableE2=1 --enableControl=1 --realtime=1 \
  --ricIp=127.0.0.1 --ricPort=36421 \
  --plugins=integration/build/phase06-plugins
```

After Setup completes, terminal 4 sends the private test control to gNB 102:

```sh
HERO_TARGET_GNB=102 HERO_MAX_DL_MCS=1 ./integration/scripts/start-control-xapp.sh
```

Check native output for two accepted Setup responses, distinct `gnb_id` indication samples, and `[HERO RC PRIVATE] ... applied=1`. Check monitor output for decoded HERO DL/UL throughput, and compare its rounded values with the matching native sample and interval in the log/CSV. The control changes only the selected BWP scheduler's `MaxDlMcs`; it is a local extension, not a standardized RC operation.

## Configurable native scenarios

```sh
integration/build/native-measurements --gnbs=1 --ues=4 --duration=2 --csv=one.csv
integration/build/native-measurements --gnbs=2 --ues=6 --duration=2 --csv=two.csv
integration/build/native-measurements --gnbs=3 --ues=9 --duration=2 --csv=three.csv
```

For live E2, add `--enableE2=1 --realtime=1 --ricIp=127.0.0.1 --ricPort=36421`; add `--enableControl=1` to advertise the private control extension. The one-gNB default preserves the Phase 03 UE placement/workload.

Validation scripts preserve logs, association snapshots, CSVs, and overhead JSON under `integration/build/`. Override the RIC configuration with `FLEXRIC_CONFIG=/path/to/flexric.conf`. To use different source/build trees, pass `-DNS3_SOURCE_DIR=... -DNS3_BUILD_DIR=... -DFLEXRIC_SOURCE_DIR=... -DFLEXRIC_BUILD_DIR=...` when configuring CMake; the shell runners also accept `HERO_INTEGRATION_BUILD_DIR` and `FLEXRIC_BUILD_DIR` for their runtime artifact/build paths.
