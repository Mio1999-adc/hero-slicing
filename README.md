# HERO-Slicing

Hierarchical, PHY-aware RL/FL framework for UE-aware RAN slicing in Open RAN.
This README starts with installing the platform it runs on (asn1c, FlexRIC, ns-O-RAN).
Project overview and the Python quick start are at the end.

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

## 5. Project overview

```
Tier 3  Global HERO     federated aggregation of regional models
Tier 2  Regional HERO   Near-RT RIC, multi-gNB coordination
Tier 1  Local HERO      per-gNB PPO agent -> Slice Manager -> MAC scheduler
```
Loop: KPMs -> state -> policy -> slice ratio rho -> Slice Manager -> RB budget
`floor(rho_s * N_RB)` -> MAC scheduler -> PHY -> new KPMs. HERO controls slice-level ratios
only; the MAC scheduler keeps UE/RB/MCS scheduling.

### Status

| Part | Status |
|---|---|
| Python HERO stack (state builder, Slice Manager, PPO, regional/global FL, 7 baselines, S1-S8) | Implemented, runs against a **mock RAN adapter** (not ns-3) |
| Tests | 26 passed, 1 skipped (live FlexRIC/E2 test, needs SCTP) in the development sandbox |
| Local web console (FastAPI, port 8090) | Implemented |
| Real ns-3 / FlexRIC integration | **Not implemented yet.** `FlexRICAdapter` is only an interface to fill in |

### Layout

```
HERO-slicing/
├── src/hero/    env, state, slice_manager, policy, reward, local, regional,
│                global_hero, fl, baselines, scenarios, metrics, runner, worker, cli
├── web/         FastAPI backend + HTML frontend
├── tests/       pytest suite
├── scripts/     install, build, run and test scripts
├── docs/        analysis, state availability, modifications, test report
└── platforms/   upstream code from sections 2-4 (not committed)
```

### Python quick start

```bash
git clone https://github.com/Mio1999-adc/hero-slicing.git
cd hero-slicing
pip install -r requirements.txt
./scripts/run_tests.sh
./scripts/run_experiment.sh --method hero_slicing --scenario S5 --steps 300
./scripts/run_web.sh            # open http://localhost:8090
```

### Planned ns-O-RAN changes (not done yet)

| Change | File area |
|---|---|
| Slice-ratio parameter under E2SM-RC `Radio_Resource_Allocation_Control` | `contrib/oran-interface/model/ric-control-*` |
| Implement the stubbed slice ID (S-NSSAI) in KPM indications | `contrib/oran-interface/model/kpm-indication.*` |
| Slice Manager class on the gNB side | new file in `contrib/oran-interface/model/` |
| Filter the candidate RB set by slice budget in the PF scheduler | `src/mmwave/model/mmwave-flex-tti-pf-mac-scheduler.cc` |

## License

Add a `LICENSE` file before publishing. Upstream projects keep their own licenses.
