#!/usr/bin/env python3
"""Run original FlexRIC + stock KPM monitor + native LENA measurement source."""
import argparse
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--logs', type=Path, default=ROOT / 'integration/build/phase04-validation')
parser.add_argument('--ric', type=Path, default=ROOT / 'platforms/flexric/build/examples/ric/nearRT-RIC')
parser.add_argument('--xapp', type=Path, default=ROOT / 'platforms/flexric/build/examples/xApp/c/monitor/xapp_kpm_moni')
parser.add_argument('--build', type=Path, default=ROOT / 'integration/build')
parser.add_argument('--gdb-native', action='store_true', help='run the native child under batch gdb')
parser.add_argument('--duration', type=int, default=8)
args = parser.parse_args()
args.logs.mkdir(parents=True, exist_ok=True)
plugins = args.build / 'phase02-plugins'
ric_config = Path(os.environ.get('FLEXRIC_CONFIG', ROOT / 'integration/config/flexric-local.conf'))

for port in (36421, 36422):
    with socket.socket(socket.AF_INET, socket.SOCK_SEQPACKET, socket.IPPROTO_SCTP) as probe:
        probe.bind(('127.0.0.1', port))

procs = []
handles = []
def start(name, command, env=None):
    print('COMMAND:', ' '.join(map(str, command)), flush=True)
    output = (args.logs / name).open('w')
    handles.append(output)
    proc = subprocess.Popen(list(map(str, command)), cwd=ROOT, env=env,
                            stdout=output, stderr=subprocess.STDOUT)
    procs.append(proc)
    return proc

def stop(proc, sig=signal.SIGINT):
    if proc.poll() is None:
        proc.send_signal(sig)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)

try:
    ric = start('ric.log', ['stdbuf', '-oL', '-eL', args.ric, '-c',
                            str(ric_config), '-p', str(plugins) + '/',
                            '-a', '127.0.0.1'])
    ready = False
    for _ in range(100):
        if ric.poll() is not None:
            raise RuntimeError(f'RIC exited early: {ric.returncode}')
        eps = Path('/proc/net/sctp/eps').read_text().splitlines()
        if eps:
            pidx = eps[0].split().index('LPORT')
            ready = any(int(line.split()[pidx]) == 36421 for line in eps[1:])
        if ready:
            break
        time.sleep(0.05)
    if not ready:
        raise RuntimeError('Original RIC SCTP listener did not become ready')

    xapp_env = os.environ.copy()
    xapp_env['XAPP_DURATION'] = '30'
    xapp = start('xapp-kpm-monitor.log', ['stdbuf', '-oL', '-eL', args.xapp,
                                         '-p', str(plugins) + '/'], xapp_env)
    time.sleep(1)
    native_cmd = [args.build / 'native-measurements', '--ues=3', '--sampleMs=1000',
                  '--duration=' + str(args.duration), '--csv=' + str(args.logs / 'native-measurements.csv'),
                  '--enableE2=1', '--realtime=1', '--ricIp=127.0.0.1', '--ricPort=36421']
    if args.gdb_native:
        native_cmd = ['gdb', '--batch', '-ex', 'run', '-ex', 'thread apply all bt', '--args', *native_cmd]
    native = start('native-lena.log', native_cmd)
    assoc_samples = []
    xapp_stopped = False
    deadline = time.monotonic() + 120
    while native.poll() is None and time.monotonic() < deadline:
        assoc_samples.append(Path('/proc/net/sctp/assocs').read_text())
        monitor_log = (args.logs / 'xapp-kpm-monitor.log').read_text()
        if not xapp_stopped and monitor_log.count('KPM ind_msg latency') >= 2:
            # Exercise the stock xApp's subscription deletion while the E2
            # association is still live so its synchronous delete response can
            # complete before the gNB and RIC shut down.
            stop(xapp)
            xapp_stopped = True
        time.sleep(0.1)
    if native.poll() is None:
        stop(native, signal.SIGTERM)
        raise RuntimeError('native LENA E2 measurement scenario timed out')
    if native.returncode != 0:
        raise RuntimeError(f'native LENA scenario exited {native.returncode}')
    (args.logs / 'sctp-associations.log').write_text('\n'.join(assoc_samples))
    if not xapp_stopped:
        raise RuntimeError('stock KPM xApp did not receive two indications while the gNB was running')
    if xapp.returncode != 0:
        raise RuntimeError(f'stock xApp shutdown/subscription deletion exited {xapp.returncode}')
    stop(ric)
finally:
    for proc in reversed(procs):
        stop(proc)
    for handle in handles:
        handle.close()

native_log = (args.logs / 'native-lena.log').read_text()
xapp_log = (args.logs / 'xapp-kpm-monitor.log').read_text()
ric_log = (args.logs / 'ric.log').read_text()
print('--- RIC ---\n' + ric_log)
print('--- NATIVE ---\n' + native_log)
print('--- STOCK KPM XAPP ---\n' + xapp_log)
assert '[HERO E2] SCTP_ESTABLISHED' in native_log
assert 'E2_SETUP_RESPONSE accepted RAN function ID=2' in native_log
assert 'subscription admitted' in native_log
assert 'E2 SETUP-REQUEST rx from PLMN 50501 Node ID 101' in ric_log
assert 'Accepting RAN function ID 2' in ric_log
assert 'RIC SUBSCRIPTION REQUEST' in ric_log or 'subscription' in ric_log.lower()
assert 'HERO.DL.PDCP.SDU.Throughput.Mbps =' in xapp_log
assert 'HERO.UL.PDCP.SDU.Throughput.Mbps =' in xapp_log
assert ('PASS native multi-gNB DL/UL measurement scenario' in native_log
        or 'PASS native DL/UL measurement scenario' in native_log)

native_samples = {}
for match in re.finditer(r'\[HERO KPM\] encoded native sample sequence=(\d+).*?dl_pdcp_sdu_mbps=([0-9.eE+-]+) ul_pdcp_sdu_mbps=([0-9.eE+-]+)', native_log):
    native_samples[int(match.group(1))] = (float(match.group(2)), float(match.group(3)))
assert native_samples, 'No native KPM samples were encoded'
decoded_pairs = [(float(dl), float(ul)) for dl, ul in re.findall(
    r'HERO\.DL\.PDCP\.SDU\.Throughput\.Mbps = ([0-9.eE+-]+).*?'
    r'HERO\.UL\.PDCP\.SDU\.Throughput\.Mbps = ([0-9.eE+-]+)', xapp_log, re.S)]
assert decoded_pairs, 'Stock monitoring xApp decoded no HERO DL/UL pair'
# Compare a complete decoded indication pair to one complete provider sample;
# the xApp prints both values to two decimals.
matched = next(((seq, native_values) for seq, native_values in native_samples.items()
                if (round(native_values[0], 2), round(native_values[1], 2)) in decoded_pairs), None)
assert matched, f'No same-indication xApp pair matched a native sample: {native_samples}, {decoded_pairs}'
print(f'PASS phase 04: KPM indication decoded by original xApp; matched native sample sequence={matched[0]} DL={matched[1][0]:.2f} Mbps UL={matched[1][1]:.2f} Mbps')
