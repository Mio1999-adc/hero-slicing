# Multi-gNB architecture

`native-measurements --gnbs=1..3 --ues=N` creates one native LENA device, one measurement provider, and (when enabled) one E2 agent for each gNB. Each agent gets a distinct configured gNB ID (`101 + index` in the example) and owns its association state, KPM sample sequence, provider pointer, control queue, and shutdown. The native `simulationNodeId`, cell ID, PLMN, and E2 gNB ID remain distinct values.

UEs are assigned to spatial clusters and attached through native `NrHelper::AttachToClosestGnb`. Each provider builds its UE records from the actual serving cell at sample time and includes records only for its own cell. Cell-level aggregation is derived from those per-cell records. No global measurement counter or subscription sequence is shared across gNB agents.

Each original FlexRIC worker runs with a thread-local pointer to its agent context. The callback table and service-model implementations are code-shared, while identity, KPM callback context and RC callback context are per agent. The local RC xApp targets a specific connected E2 node identity; the command reaches only that agent's queue. Tests verify that a request to gNB 102 caps its samples and leaves gNB 101 unchanged.

The failure-isolation runner points gNB 102 at an unused SCTP port while gNB 101 connects to the RIC. gNB 102 records its own failed state; gNB 101 registers and the radio simulation completes. A live two-gNB RIC/KPM/control scenario passes. Three-gNB radio-only operation and three E2 Setup registrations pass, but the installed stock KPM monitoring xApp aborts while handling three concurrent subscriptions. Three-node indication reporting remains unsupported/unverified with that xApp.
