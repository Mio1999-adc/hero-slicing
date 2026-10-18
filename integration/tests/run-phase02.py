#!/usr/bin/env python3
"""Validate original RIC + actual native LENA example; owns only child processes."""
import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--logs', type=Path, default=root / 'integration/logs/phase02')
parser.add_argument('--ric', type=Path, default=root / 'platforms/flexric/build/examples/ric/nearRT-RIC')
parser.add_argument('--build', type=Path, default=root / 'integration/build')
args = parser.parse_args()
args.logs.mkdir(parents=True, exist_ok=True)
plugins = args.build / 'phase02-plugins'
ric_config = Path(os.environ.get('FLEXRIC_CONFIG', root / 'integration/config/flexric-local.conf'))

def run(name, command, expected=0):
    print('COMMAND:', ' '.join(map(str, command)), flush=True)
    with (args.logs / name).open('w') as output:
        result = subprocess.run(list(map(str, command)), cwd=root, stdout=output, stderr=subprocess.STDOUT, timeout=40)
    content = (args.logs / name).read_text()
    print(content, flush=True)
    if result.returncode != expected:
        raise RuntimeError(f'{name}: exit {result.returncode}, expected {expected}')
    return content

run('identity-registration.log', [args.build / 'e2-identity-registration'])
run('network-lifecycle.log', [args.build / 'e2-network-lifecycle', plugins])
# Check required ports before creating our RIC. Never stop an existing service.
for port in (36421, 36422):
    with socket.socket(socket.AF_INET, socket.SOCK_SEQPACKET, socket.IPPROTO_SCTP) as probe:
        probe.bind(('127.0.0.1', port))
command = ['stdbuf', '-oL', '-eL', str(args.ric), '-c', str(ric_config), '-p', str(plugins) + '/', '-a', '127.0.0.1']
print('COMMAND:', ' '.join(command), flush=True)
with (args.logs / 'ric.log').open('w') as output:
    ric = subprocess.Popen(command, cwd=root, stdout=output, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(100):
            if ric.poll() is not None:
                raise RuntimeError(f'RIC exited early: {ric.returncode}')
            lines = Path('/proc/net/sctp/eps').read_text().splitlines()
            if lines:
                columns = lines[0].split()
                port_index = columns.index('LPORT')
                ready = any(int(line.split()[port_index]) == 36421 for line in lines[1:])
            if ready:
                break
            time.sleep(0.05)
        if not ready:
            raise RuntimeError('Original RIC SCTP listener not ready within 5 s')
        example = [str(args.build / 'single-gnb-e2'), '--ricIp=127.0.0.1', '--ricPort=36421', '--gnbId=101', '--mcc=505', '--mnc=1', '--mncDigits=2', '--duration=2', '--realtime=1']
        print('COMMAND:', ' '.join(example), flush=True)
        with (args.logs / 'native-lena.log').open('w') as native_output:
            native = subprocess.Popen(['stdbuf', '-oL', '-eL', *example], cwd=root, stdout=native_output, stderr=subprocess.STDOUT)
            try:
                snapshots = []
                deadline = time.monotonic() + 15
                while native.poll() is None and time.monotonic() < deadline:
                    snapshots.append(f'wall_monotonic={time.monotonic()}\n' + Path('/proc/net/sctp/assocs').read_text())
                    time.sleep(0.1)
                native.wait(timeout=2)
                (args.logs / 'sctp-associations.log').write_text('\n'.join(snapshots))
                if native.returncode:
                    raise RuntimeError(f'Native LENA example failed: {native.returncode}')
            finally:
                if native.poll() is None:
                    native.terminate()
                    native.wait(timeout=5)
    finally:
        if ric.poll() is None:
            ric.send_signal(signal.SIGINT)
            try:
                ric.wait(timeout=8)
            except subprocess.TimeoutExpired:
                ric.terminate()
                ric.wait(timeout=5)
ric_log = (args.logs / 'ric.log').read_text()
native_log = (args.logs / 'native-lena.log').read_text()
print(ric_log, native_log, flush=True)
assert ric.returncode == 0, f'RIC shutdown failed: {ric.returncode}'
assert 'E2 SETUP-REQUEST rx from PLMN 50501 Node ID 101' in ric_log
assert 'Accepting RAN function ID 2' in ric_log
assert 'SCTP_ESTABLISHED' in native_log
assert 'E2_SETUP_RESPONSE accepted RAN function ID=2' in native_log
assert 'radio=PASS E2=REGISTERED' in native_log
assert 'shutdown joined worker' in native_log
print('PASS: original Near-RT RIC accepted native LENA gNB 101 / PLMN 50501 / KPM ID 2; radio delivery and cleanup passed', flush=True)
