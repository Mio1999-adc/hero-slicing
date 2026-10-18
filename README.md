# HERO-Slicing

Hierarchical, PHY-aware RL/FL framework for UE-aware RAN slicing in Open RAN.
The repository includes an independent integration layer under `integration/` for the
installed native ns-3/5G-LENA and original FlexRIC trees. This README records the
current integration state and the opt-in LENA-side slice-enforcement and PPO extension.

## 1. Versions and original repositories

`platforms/` is in `.gitignore`. Clone the upstream projects yourself at these versions.

| Component | Original repository | Version used |
|---|---|---|
| asn1c (mouse07410 fork) | https://github.com/mouse07410/asn1c | `940dd5fa9f3917913fd487b13dfddfacd0ded06e` **(UNVERIFIED, see note)** |
| FlexRIC | https://gitlab.eurecom.fr/mosaic5g/flexric | `f56d4487507c7ace392f1ec3f598e7eeda670acc` (branch `oie-ric-taap-xapps`), as pinned by the ns-O-RAN-flexric README |
| ns-O-RAN-flexric (umbrella) | https://github.com/Orange-OpenSource/ns-O-RAN-flexric | `1398bb8d70791b8ee342da5101bc66fad01a6a86` |
| e2sim-kpmv3 (submodule) | https://github.com/MinaYonan123/e2sim-kpmv3 | `732d64703bb7f4ba585c434a133cf4cf23972298` |
| mmwave-LENA-oran (submodule, ns-3 3.42) | https://github.com/MinaYonan123/mmwave-LENA-oran | `3b4c4f10d46129ba2e8ca382374459ba27051fdb` |
| oran-interface (nested submodule) | https://github.com/MinaYonan123/oran-interface | `95fba5add42ac1c05e2bbde1d3d88329ba35bdc9` |
| nr / 5G-LENA mirror (nested submodule) | https://github.com/MinaYonan123/ns3-oran-lena-nr | `35eeab6de3548bd838d95502af0382bd7bc64c39` |

FlexRIC build flags: `-DE2AP_VERSION=E2AP_V1 -DKPM_VERSION=KPM_V3_00`.

> **asn1c note:** this commit was taken from memory of the OpenAirInterface build scripts
> and has not been confirmed against this FlexRIC checkout. If `git checkout` cannot find
> it, or the FlexRIC build still shows `did you mean to use '->'` errors, replace it with a
> commit that works and update this table.

Targeted OS: Ubuntu 22.04 with gcc-10 (see the compiler note in the troubleshooting section).
Run each block as separate commands, not as one pasted line.

## 2. Install asn1c

Do **not** use `apt install asn1c`. It is the old 0.9.28 release and fails with
`-gen-UPER: Invalid argument`. The fork's newest branch (`vlm_master`) generates RRC
structure members as pointers, which breaks FlexRIC with `did you mean to use '->'`
errors. Use the pinned commit.

```bash
conda deactivate 2>/dev/null || true     # conda's swig/asn1c can shadow system tools

sudo apt-get update
sudo apt-get install -y build-essential git cmake cmake-curses-gui autoconf automake \
  autotools-dev libtool bison byacc flex libsctp-dev libpcre2-dev \
  python3 python3-dev python-dev-is-python3 swig
cmake --version     # >= 3.15
swig -version       # >= 4.0 (otherwise build swig from source)

sudo apt-get remove -y asn1c
cd ~
[ -d asn1c ] || git clone https://github.com/mouse07410/asn1c.git
cd asn1c
git fetch --all
git checkout 940dd5fa9f3917913fd487b13dfddfacd0ded06e
git log -1 --oneline                       # must print a commit
make distclean 2>/dev/null || true
autoreconf -iv
./configure
make -j"$(nproc)"
sudo make install
sudo ldconfig
hash -r
```

Verify:
```bash
which asn1c                                  # /usr/local/bin/asn1c
asn1c -help 2>&1 | grep -i -E "uper|aper"    # lists -gen-UPER and -gen-APER
```

## 3. Install FlexRIC

```bash
mkdir -p platforms && cd platforms
git clone https://gitlab.eurecom.fr/mosaic5g/flexric.git
cd flexric
git checkout f56d4487507c7ace392f1ec3f598e7eeda670acc

rm -rf build && mkdir build && cd build        # always a clean build directory
cmake .. -DE2AP_VERSION=E2AP_V1 -DKPM_VERSION=KPM_V3_00
make -j2 2>&1 | tee build.log
tail -3 build.log                # expect "[100%] Built target ..."
grep -c "error:" build.log       # expect 0
sudo make install
```
Wipe `build/` whenever asn1c changes. The generated sources in it come from the asn1c
that was installed when you first configured.

Check SCTP, then test:
```bash
python3 -c "import socket; socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_SCTP); print('SCTP ok')"
# if it fails: sudo modprobe sctp

cd build && ctest
```
In three terminals from `platforms/flexric`:
```bash
./build/examples/ric/nearRT-RIC
./build/examples/emulator/agent/emu_agent_gnb_cu
./build/examples/xApp/c/monitor/xapp_kpm_moni
```
FlexRIC works when the RIC logs an E2 setup from the agent and the xApp prints KPM indications.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `-gen-UPER: Invalid argument` | apt asn1c (old) | install the fork, section 2 |
| `did you mean to use '->'` in `sqlite3_wrapper.c`, `xapp_rc_moni.c` | asn1c newer than FlexRIC expects | pin asn1c, rebuild from a clean `build/` |
| `_Static_assert ... not constant` in `src/util/eq.h` | gcc 11 or newer | use gcc-10: `CC=gcc-10 CXX=g++-10 cmake ...` |
| `collect2: ld returned 1` on many xApps | library failed to compile earlier | fix the first `error:` in `build.log` |
| `Error finding path /usr/local/lib/flexric/` | `make install` not run | `sudo make install` |
| swig/asn1c resolve inside `anaconda3` | conda shadowing | `conda deactivate` |

**Compiler note:** the gcc-10 requirement comes from a community FlexRIC v1.0.0 install guide
for Ubuntu 22.04 (https://hackmd.io/@RaffieWinata/BJ0bfH2TT). In a separate build with GCC 13.3, the
same `_Static_assert` failure appeared, and it was avoided by changing the `__GNUC__` branch
of `eq_ptr_assert` in `src/util/eq.h` to a runtime `assert()`. Prefer gcc-10 over patching.

## 4. Install ns-O-RAN (ns-3 side)

```bash
cd platforms
git clone https://github.com/Orange-OpenSource/ns-O-RAN-flexric.git
cd ns-O-RAN-flexric
git checkout 1398bb8d70791b8ee342da5101bc66fad01a6a86
git submodule update --init mmwave-LENA-oran
(cd mmwave-LENA-oran && git submodule update --init --recursive)
git submodule update --init e2sim-kpmv3        # without --recursive, see note
```
`git submodule update --init --recursive` at the top level can abort on a broken nested
submodule inside `e2sim-kpmv3/.../kpm_e2sm/nlohmann_json_release`. That example is not
needed, so initialise `e2sim-kpmv3` without `--recursive`.

Extra packages beyond FlexRIC's:
```bash
sudo apt-get install -y libboost-all-dev nlohmann-json3-dev libeigen3-dev \
  libcurl4-openssl-dev sqlite3 libsqlite3-dev libgsl-dev
```

Build e2sim, then ns-3:
```bash
cd e2sim-kpmv3/e2sim && mkdir -p build && cd build
cmake .. -DDEV_PKG=1 -DLOG_LEVEL=2
make package -j2
sudo dpkg --install ./e2sim-dev_1.0.0_amd64.deb && sudo ldconfig

cd ../../../mmwave-LENA-oran
./ns3 configure --enable-examples
./ns3 build
```
A full ns-3 build is long (hours on a single core). `./ns3 build core`,
`./ns3 build oran-interface` and `./ns3 build mmwave` build the modules HERO needs.

---

## 5. Native LENA slicing: current state and extension boundary

### Existing platform

The working integration is the independent code in [`integration/`](integration/README.md),
which uses the installed native ns-3/5G-LENA and original FlexRIC sources under the ignored
`platforms/` directory. It provides native radio scenarios, per-gNB/UE measurements,
E2 Setup, the currently supported KPM v3 reporting path, a private `MaxDlMcs` control
extension, and a dashboard. The dashboard's verified baseline radio profile is one BWP.
See [Phase 08 configuration and validation](integration/docs/phase08-configuration-manager.md)
for the exact settings and runtime limitations.

The native scenario now supports opt-in explicit or seeded-random UE associations to
eMBB, URLLC, and mMTC, per-slice UDP traffic profiles, supported dedicated EPS bearers,
and slice/QoS metadata in native UE measurement rows. The baseline remains the default.
These UE labels and bearers do **not** enforce slice resource budgets: the installed
native MAC scheduler remains the default. When opt-in slice enforcement is selected,
each gNB owns a `SliceBudgetManager` and an OFDMA PF-derived scheduler applies per-slice
RBG-symbol upper caps. Optional live local or regional PPO consumes actual native
measurement windows and updates those per-gNB budgets on the ns-3 event thread. The
existing private `MaxDlMcs` operation is not a slice-budget API. The dashboard shows UE
slice assignments and measured throughput; application delay and reliability
satisfaction remain unavailable.
See [slice UE and traffic profiles](integration/docs/slice-ue-traffic.md) for exact
commands, measurement meanings, and the remaining scheduler boundary.

### Intended native data path

```text
UE QoS flow / bearer
        │  explicit UE or logical-channel → slice association
        ▼
gNB slice manager ── validated per-slice budget ──► opt-in slice-aware MAC scheduler
        ▲                                               │
        │ PPO ratio update via simulation-thread API     │ existing UE/RBG/MCS policy
        └───────────────────────────────────────────────┘
                                                        ▼
                                                 native LENA PHY
                                                        │
                                         existing measurements / E2 KPM
```

Logical slices are an additional resource-control layer; they are not automatically
equivalent to physical BWPs. The native BWP Manager continues to route bearers to BWPs
using its existing configuration, while a slice association identifies which logical
resource budget applies to a UE or logical channel. A scenario may later choose a BWP
layout suitable for an experiment, but the current verified profile remains one BWP.

The current opt-in scheduler preserves native PF UE selection and caps each slice's
RBG-symbol allocation. Ratios are validated as a three-slice simplex and converted to
per-symbol RBG caps; DL and UL are enforced independently. This is an upper cap: it does
not guarantee minimum allocations, borrow unused capacity, reassign BWPs, or implement
the paper's unspecified feasibility/rounding optimization. The default native PF
scheduler remains available as the baseline. See [slice manager](integration/docs/slice-manager.md)
for exact units and limitations.

### Incremental implementation plan

| Boundary | Intended responsibility | Status |
|---|---|---|
| Slice association | Map a UE and, where needed, its LCID/QoS flow to a logical slice; keep 5QI as QoS treatment, not slice identity | Opt-in UE-level IDs 1–3; per-flow mapping pending |
| gNB Slice Manager | Own per-gNB slice configuration, validate ratio budgets, and expose usage/acceptance state | Opt-in `SliceBudgetManager`; accepts live local/regional PPO ratios |
| Scheduler catalog | Keep the existing scheduler as baseline and provide an explicit opt-in slice-aware scheduler selection | `NrMacSchedulerHeroSlicePF` derives from native OFDMA PF; explicit runtime flag |
| MAC enforcement | Apply budgets during native RBG allocation without replacing the selected scheduler's UE-level policy | Experimental per-slice DL/UL upper caps; no minimum guarantee or unused-budget borrowing |
| HERO input | Connect local/regional PPO policies through simulation-thread-safe dispatch | Live sidecar action loop updates the correct per-gNB manager from native measurement windows; synchronous IPC can slow real-time execution |
| Telemetry | Preserve existing KPM/E2 and local native measurement paths; add slice metrics only after their native definitions exist | Local CSV/dashboard carries UE slice/profile metadata; E2 KPM remains unchanged |

The implemented extension is a modular native `contrib/nr` addition with explicit build
registration and scenario selection. It does not depend on Orange ns-O-RAN source.
Existing integration, original LENA BWP management, bearer handling, PHY/MAC behavior,
and FlexRIC components remain intact. The default baseline is preserved. See the slice
manager and PPO validation commands in [`integration/README.md`](integration/README.md);
unused-budget borrowing, minimum guarantees, handover migration, and E2 slice control
remain outside the implementation. No HERO learning equations were changed.

### Repository layout

```
HERO-slicing/
├── integration/       Independent native ns-3/LENA + original FlexRIC integration
│   ├── dashboard/     Configuration, launch, visualization, and monitoring UI
│   ├── learning/      Local/regional PPO, live measurement controller, and KPM state builders
│   ├── e2-agent/      Original FlexRIC-backed simulator-side E2 agent
│   ├── kpm/           Native LENA measurement collection and CSV export
│   ├── lena-adapter/  gNB lifecycle and simulator bridge
│   ├── control/       Currently supported private control extension
│   └── docs/          Compatibility, validation, and known limitations
├── platforms/         Installed upstream source/build trees; ignored by Git
└── README.md
```

Build and run the existing integration with the commands in
[`integration/README.md`](integration/README.md). Slice-level MAC enforcement is a
separate opt-in mode in the current build and dashboard controls; see
[`slice-manager.md`](integration/docs/slice-manager.md). Live PPO ratio control is
documented in [`ppo-learning.md`](integration/docs/ppo-learning.md); the native PF
baseline remains the default.
The PPO actor/critic, local/regional measurement-state builders, and live native-window
controller are available under [`integration/learning`](integration/learning) and documented in
[`ppo-learning.md`](integration/docs/ppo-learning.md). When PPO is enabled, sampled or
inferred actions replace initial dashboard ratios after each real measurement window.
For the module map and learning data/action flow relative to native LENA and FlexRIC,
see [`HERO-Slicing learning architecture`](integration/docs/hero-learning-architecture.md).

## License

Add a `LICENSE` file before publishing. Upstream projects keep their own licenses.
