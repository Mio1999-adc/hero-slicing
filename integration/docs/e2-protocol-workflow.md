# E2 protocol workflow

This integration uses native 5G-LENA devices and original FlexRIC protocol/service-model code. It does not use an emulated gNB.

1. `native-measurements` creates and configures the native `NrGnbNetDevice` instances, attaches UEs, assigns each gNB a unique PLMN/gNB ID, and creates one `FlexricE2Agent` per gNB.
2. Each agent creates its own original FlexRIC `e2_agent_t` and worker, opens the configured KPM plugin (and optional private RC plugin), associates to the RIC over SCTP, and transmits an E2 Setup Request.
3. FlexRIC's E2AP ASN.1 implementation encodes the setup procedure. The integration accepts registration only when KPM v3 RAN function ID 2 is in the Setup Response. When private control is enabled, RC function ID 3 must also be accepted.
4. A KPM xApp asks the Near-RT RIC to subscribe to RAN function ID 2. The agent decodes and validates one periodic Report action: supported HERO DL/UL throughput names, `noLabel`, and an interval matching a provider sample. FlexRIC owns action lifecycle, subscription response, timer and deletion.
5. On an indication callback, the protocol worker reads the provider's immutable latest sample. It never reads ns-3 objects. Original FlexRIC's KPM v3 implementation encodes the indication and original E2AP code sends it over the association.
6. When enabled, the private control xApp resolves the requested connected E2 node and sends the local RC extension. The worker validates and copies its parameters to the per-agent queue. An ns-3 event drains that queue and applies `MaxDlMcs` on the selected native gNB's BWP 0 scheduler. The callback returns the local service-model outcome; the E2AP control acknowledgement alone is not treated as proof of application.
7. On shutdown, each agent stops admission, joins its protocol worker, releases its SCTP endpoint and plugin references, closes its control queue, and cancels further simulator queue pumping before native devices are destroyed.

The simulator runs in ns-3 realtime mode for live E2 tests, so E2/FlexRIC events use wall-clock scheduling while measurements carry simulation-time interval bounds. Control crosses threads only as copied owned data. Scheduler access occurs on the simulation thread. Automatic SCTP reconnect is not implemented; a lost association enters a terminal failed state for that agent.

The installed FlexRIC RC standard operation for bearer/QoS flow mapping is not mapped to a LENA scheduler operation. The tested `MaxDlMcs` request is a documented local extension and is not an O-RAN-standard scheduler control style.
