# Phase 01 validation report

Date: 2026-10-13. Build environment: Ubuntu 24.04.5, GNU C/C++ 13.3.0. Tested against the revisions and configurations recorded in api-compatibility.md.

## Commands and results

From `/home/hpclab-lananh/HERO-slicing`:

```bash
integration/tests/run-smoke.sh
# Equivalent commands:
cmake -S integration -B integration/build
cmake --build integration/build -j2
ctest --test-dir integration/build --output-on-failure

ctest --test-dir platforms/flexric/build \
  -R 'Unit_test_enc_dec_(E2AP|KPM|RC)' --output-on-failure
platforms/ns-3/build/utils/ns3.42-test-runner-debug --suite=nr-test-sched
platforms/ns-3/build/utils/ns3.42-test-runner-debug --suite=nr-test-notching
```

Final outcomes: integration configure/build succeeded; smoke 1/1 passed; original FlexRIC codec suites 3/3 passed; native scheduler and notching suites passed. These are focused checks, not full upstream regression suites.

The smoke checks all proposed abstract interface types, rejects a null adapter device, constructs a native gNB via NrHelper with one BWP, verifies cell/BWP/scheduler access, dispatches a simulation event from a foreign thread using ScheduleWithContext, runs for one simulated microsecond and destroys the simulator. It compiles actual native measurement/UE/RRC headers. The C probe takes addresses of actual agent, transport, KPM and RC functions and links original archives; none of those functions is invoked.

The smoke does not connect to RIC, start an agent, load plugins, encode/decode live payloads, attach measurement traces or apply scheduler controls. There are no UE traffic or multi-gNB execution claims. Existing codec suites exercise upstream encode/decode behavior independently.

## Findings during verification

- The first smoke used a bare NrGnbNetDevice and aborted during disposal because required native RRC/carrier manager objects were absent. Replaced with helper-wired native gNB; final lifecycle test passes.
- Including NrHelper required the complete NrEpcHelper type before its header because an inline Ptr default references it. Added the actual `ns3/nr-epc-helper.h` include; no upstream edit.
- The GNU linker warns that original `e2_agent.c.o` requires an executable stack. This warning originates in the installed archive; it was not patched or suppressed. The build and test pass, but a future deployment audit should account for this property.
- An upstream ns-3 header emitted an unused-parameter warning during adapter compilation; no interface compilation errors remain.
- Host SCTP socket support passed in the prior inspection outside the sandbox. It was not a RIC connection test and is not needed by this phase's smoke.

## Source inspection commands

Used `rg --files`, targeted `rg -n` and source reads (`cat`/`sed`) across original agent, service-model, LENA, core simulator, examples and CMake files. Verified each component using `git -C <source> rev-parse HEAD`, `git describe --tags`, `git status --short`, `git diff --stat` and untracked-file inventory. Read existing CMakeCache files, shared/static library paths and original emulator link.txt; no API was selected from directory names alone.

Every source link in the four design documents was checked for file existence. These documents distinguish source-inspected candidates from compiled/link-probed APIs and runtime-tested behavior.

## Files and Git summary

Created only under `integration/`: standalone CMakeLists and README, common interfaces header, LENA adapter header/implementation, smoke C++ program, C11 symbol probe, run-smoke script, four requested design documents and this validation report. Empty `e2-agent/`, `kpm/`, `control/` and `examples/` directories reserve future module boundaries; Git does not track empty directories.

Root Git changes consist of the new untracked integration source/document files. No existing tracked file was modified. Upstream repositories retain empty tracked diffs and their pre-existing untracked files. Build products and generated logs are ignored. No reset, deletion of build directories, upstream configuration change or commit was performed.

## Remaining limitations

Public agent singleton/restart handling, application-ready notification, synchronous successful RC ACK behavior, unsupported subscription assertions, precise standard measurement definitions, live realtime lag policy and multi-agent isolation all need resolution in their requested later phases. Interfaces are contracts only except the read-only adapter. Phase 01 stops without E2 communication.
