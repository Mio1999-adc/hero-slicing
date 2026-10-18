# Phase 03 implementation and validation

Phase 03 adds a local native LENA measurement provider. It does not send KPM indications and does not change LENA scheduling policy.

## Implemented files

- `integration/kpm/lena-measurement-provider.h` and `.cc`: `LenaMeasurementProvider`, single native gNB/BWP scope, per-UE IMSI/RNTI generation records, PDCP/RLC/MAC/PHY callbacks, periodic ns-3 sampling, partial-window flush, thread-safe latest snapshots, and safe trace/event shutdown.
- `integration/kpm/native-measurements.h`: exact integer counter records and sample context.
- `integration/kpm/measurement-csv.h` and `.cc`: interval CSV exporter. Empty cells mean unavailable values.
- `integration/examples/native-measurements.cc`: fixed-seed EPC/radio scenario with one native gNB, three native UEs, real UDP DL and UL traffic, sampling, CSV output and a foreign-thread reader of published immutable snapshots.
- `integration/tests/verify-native-measurements.py`: checks exact traffic counters, UE-to-cell identity, additive cell totals, units/formulas, grant limits, repeatability, observer effect and partial-window handling.
- `integration/CMakeLists.txt`: measurement library, scenario and focused CTest entries.
- `integration/docs/measurement-mapping.md`: source callbacks, formulas, units, scope, sampling and unavailable/limited metrics.

## Compatibility evidence

The adapter uses the installed native LENA v3.3.1 APIs in `platforms/ns-3/contrib/nr/model`: `NrPdcp` / `NrRlc` TxPDU and RxPDU traces; `NrUeManager::DrbCreated`; `NrUePhy::DlDataSinr`; `NrGnbPhy::GnbPhyTxedCtrlMsgsTrace`, `GnbPhyRxedCtrlMsgsTrace`, and `UlSinrTrace`; `NrGnbMac::DlScheduling` / `UlScheduling`; and the gNB RRC connection lifecycle traces. It decodes PRB grant sizes from native DL/UL DCI RBG masks and the installed PHY's RB-per-RBG and RB capacity. It does not infer allocated PRBs from BWP bandwidth. RLC/MAC buffer occupancy is unavailable and exported as missing.

RRC bearer traces lack BWP attribution, so the provider rejects multi-BWP devices rather than guessing. Initial-access allocations with no IMSI are omitted from user-data grant counters. SINR units remain linear ratios. No standard E2SM-KPM mapping is asserted.

## Commands and results

Build command:

```sh
cmake --build integration/build -j2
```

Focused validation command:

```sh
ctest --test-dir integration/build --output-on-failure -R 'native-measurements|verify-native-measurements'
```

The CTest traffic scenario passed. The Python verifier passed with the following recorded results from its repeated fixed-seed runs:

- 3 UEs, 20 complete 100 ms samples, 80 CSV rows per run (cell + three UE rows).
- 300/300 DL UDP packets and 240/240 UL UDP packets delivered.
- 162,000 DL and 68,160 UL received PDCP SDU bytes.
- 33,390 DL and 25,440 UL PRB-grant sums; each DCI's PRB count stayed within configured RB capacity.
- Cell additive totals matched the three UE records; each UE was attributed to cell 1 with IMSIs/RNTIs 1, 2 and 3.
- Two output CSV files were byte-identical (SHA-256 `01748255519c3d2b11cdb8c64637a029021c0059cce916f6b232cab13962118c`).
- Disabling collection did not change application delivery. A 137 ms interval produced the expected final 82 ms partial sample.

Detailed reproducibility output is written to the ignored build tree at `integration/build/phase03-verification/verification.json`, with per-run CSV and logs in the same directory. The build completed successfully; emitted warnings are unused-parameter warnings in installed ns-3 headers and callback templates.

## Remaining limitations

The current provider supports one BWP per gNB and UE. UL SINR is cell-level only because the installed gNB trace has no UE identity. CQI is wideband only. Throughput is defined as received PDCP SDU bytes per elapsed sample interval and is not claimed to match a standardized active-time throughput definition. Buffer occupancy and KPM wire transmission remain outside Phase 03.
