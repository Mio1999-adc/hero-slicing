# Phase 04: KPM v3 subscription and reporting

Phase 04 adds a local KPM v3 reporting bridge on the Phase 02 FlexRIC agent and Phase 03 native measurement provider. The protocol side uses the installed original FlexRIC KPM v3 ASN.1 decoder/encoder and E2AP SCTP endpoint. The provider contributes immutable, cell-scoped native snapshots; the reporter never reads ns-3 objects from the FlexRIC worker thread.

## Advertised and accepted capability

RAN Function ID 2 advertises one Event Trigger style (style 1, format 1, periodic) and one Report style (style 1, Action Definition format 1, Indication Header format 1, Indication Message format 1). It advertises exactly these locally defined measurement names:

* `HERO.DL.PDCP.SDU.Throughput.Mbps`
* `HERO.UL.PDCP.SDU.Throughput.Mbps`

These are explicit HERO-local names, not claims that the values implement an O-RAN standardized KPM measurement definition. Each value is the native gNB-cell PDCP SDU receive-byte delta for the requested window multiplied by eight and divided by the interval and 1,000,000. DL bytes are observed at UE PDCP receive callbacks and accumulated into the serving gNB cell; UL bytes are observed at gNB PDCP receive callbacks. The report has one cell measurement row and `noLabel` for each value. This phase does not encode UE-level rows or claim standard UE identities.

Subscriptions are accepted only for function ID 2, one Report action, KPM v3 style/action format 1, a periodic interval from 1 to 10,000 ms, equal event and action granularity, and one or both supported names with exactly one `noLabel`. Duplicate names, unsupported names, unsupported labels/styles, bins, mismatched periods and unsupported action kinds are returned as subscription failures. The original FlexRIC handler schedules reports and handles subscription deletion. `kpm-subscription-compatibility` exercises supported-name, unsupported-name and mismatched-period cases.

For a requested period different from the provider's sample interval, the subscription can be admitted if its definition is otherwise supported, but indication generation safely suppresses a report until an exact native window is available. The live example configures matching 1,000 ms periods. Snapshots are copied under the provider's publication mutex; all LENA state and trace access remains on the simulator thread.

## Runtime validation

Validated against the installed native ns-3.42/5G-LENA v3.3.1 and original FlexRIC build (`E2AP_V1`, `KPM_V3_00`, ASN.1). The test uses the original `nearRT-RIC` and stock `xapp_kpm_moni`, not an E2 emulator. Run from the repository root:

```sh
cmake -S integration -B integration/build
cmake --build integration/build -j2
python3 -u integration/tests/run-phase04.py --duration=4
```

The runner first binds both stock SCTP ports (36421/36422) as a conflict check. It starts only its own RIC, xApp and LENA processes, collects `/proc/net/sctp/assocs`, waits for two decoded indications, asks the stock xApp to delete its subscription while the gNB association is live, then allows the native scenario and agent to shut down. Logs and CSV are written under `integration/build/phase04-validation/`.

The validated exchange included:

* SCTP association established to `127.0.0.1:36421`, association ID 83, 10 inbound and 10 outbound streams.
* RIC received E2 Setup from PLMN `50501`, gNB ID `101`, and accepted RAN Function ID `2`.
* The monitoring xApp subscribed to KPM and decoded two 1,000 ms indications. Its delete request received a delete response before the gNB closed.
* The native run delivered 2,100 DL and 1,500 UL UDP packets across three UEs. Native cell totals were 1,134,000 DL PDCP SDU bytes and 426,000 UL PDCP SDU bytes across four sampling windows.
* The xApp's diagnostic collection-to-receive latency was about 3.24 s for its first displayed report and 1.35 s for the second. The encoded granularity is 1,000 ms, while this RealtimeSimulatorImpl run is best effort and does not guarantee 1,000 ms wall-clock delivery under startup and CPU load.

Same-window comparison from `native-measurements.csv` and the stock xApp output:

| Sample / simulated interval | Native DL bytes | Native DL rate | xApp DL | Native UL bytes | Native UL rate | xApp UL |
|---|---:|---:|---:|---:|---:|---:|
| 1 / [0, 1 s) | 226,800 | 1.8144 Mbps | 1.81 Mbps | 84,348 | 0.674784 Mbps | 0.67 Mbps |
| 2 / [1, 2 s) | 324,000 | 2.592 Mbps | 2.59 Mbps | 121,836 | 0.974688 Mbps | 0.97 Mbps |

The displayed xApp values match the provider's values rounded to two decimals. The xApp's printed unit is `(null)` because its bundled measurement-unit table does not include these HERO-local names; the names and numeric values decode correctly. The units are encoded in the local measurement names and documented here.

## Source and lifecycle notes

`flexric-agent.c` uses the original `kpm_dec_event_trigger_asn`, `kpm_dec_action_def_asn`, `e2ap_handle_subscription_request_agent`, `e2ap_handle_subscription_delete_request_agent`, and the original KPM SM's `kpm_enc_ind_hdr` / `kpm_enc_ind_msg` path called by FlexRIC's `e2_agent.c`. `flexric-e2-agent.cc` adapts `LatestNativeSnapshot()` into the original KPM v3 C structures. It does not implement ASN.1 or E2AP encoding.

Inspection and gdb validation found a cleanup ordering issue in the installed FlexRIC agent: `e2_free_agent()` unloads its dynamic SM plugin before freeing the indication map, whose action-definition destructor is a function in that plugin. The independent C shim holds one extra `dlopen` reference to the installed `libkpm_sm.so` until the original cleanup returns. This keeps the existing callback code mapped through cleanup without modifying FlexRIC source. The live test confirmed clean agent shutdown after subscription deletion.

The stock RIC and xApp remain unchanged. No KPM indications or controls are sent after the xApp deletes its subscription. E2SM-RC control remains out of scope. Multi-gNB use, UE-scoped KPM format 3, additional metrics, and non-matching event/sample periods remain unsupported in this phase.
