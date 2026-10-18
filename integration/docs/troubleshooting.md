# Troubleshooting

| Symptom | Check | Resolution |
|---|---|---|
| CMake cannot find FlexRIC E2AP/KPM configuration | `platforms/flexric/build/CMakeCache.txt` | Use the existing build with `E2AP_V1`, `KPM_V3_00`, and ASN encodings; do not reuse a different FlexRIC profile. |
| Dashboard RIC log says `errno = 98` / `Address already in use` on port 36421 | `ss -lpn` and `/proc/net/sctp/eps` | The original `nearRT-RIC` executable exists at `platforms/flexric/build/examples/ric/nearRT-RIC`; the bind error means another process already owns its fixed E2 SCTP port. Stop the duplicate/old listener before launching a dashboard-owned RIC. The dashboard now checks listener ownership and fails clearly rather than reusing an unrelated RIC. |
| Agent reports plugin directory invalid | List `integration/build/phase02-plugins` or `phase06-plugins` | KPM-only agent mode expects `libkpm_sm.so`; control mode and the RIC expect both KPM and RC plugins. Rebuild integration to recreate links. |
| Agent connects but Setup fails or does not register KPM ID 2 | Inspect `native-lena.log` and `ric.log` | Confirm the Near-RT RIC loads the same KPM v3 plugin, the function IDs match, and no duplicate PLMN/gNB identity is active. The example uses IDs 101 onward. |
| No KPM indications | Compare subscription period, event trigger, action granularity, and `--sampleMs` | Only the two documented measurement names with `noLabel` are supported. Periods must align with complete provider windows; unsupported requests are rejected and mismatched windows are suppressed. |
| KPM xApp assertion with three gNBs | Check its E42 pending-report wait and RIC SCTP send errors | The installed stock monitor aborted in the validated three-node attempt. Use the verified two-gNB live configuration; do not report the three-node monitoring path as passed. |
| Control xApp receives ACK but no scheduler effect | Inspect native `[HERO RC PRIVATE]` outcome and following DL MCS samples | The E2AP ACK indicates transport procedure success. Confirm the selected E2 identity, private style/action, BWP 0, valid MCS range, and event-thread application result. |
| Connection loss | Inspect the affected agent's state and its gNB ID | Automatic reconnection is not implemented. Restart the simulation/agent after the RIC is available. Other agents retain separate state; the failure-isolation test demonstrates one peer can fail while another registers. |
| `tcpdump` cannot capture loopback traffic | Linux capability/sandbox error | Message lengths are measured at the original FlexRIC E2AP send boundary, so packet capture is not required for the documented APER lengths. |

See [API compatibility](api-compatibility.md), [measurement mapping](measurement-mapping.md), [control capabilities](control-capabilities.md), and [Phase 06 validation](phase06-validation.md) for supported formats, units, message sizing, and tested limitations.
