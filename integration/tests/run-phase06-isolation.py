#!/usr/bin/env python3
"""Verify one refused E2 peer does not stop another gNB's agent or radio run."""
import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser()
p.add_argument('--build',type=Path,default=ROOT/'integration/build')
p.add_argument('--ric',type=Path,default=ROOT/'platforms/flexric/build/examples/ric/nearRT-RIC')
p.add_argument('--logs',type=Path,default=ROOT/'integration/build/phase06-isolation')
a=p.parse_args(); a.logs.mkdir(parents=True,exist_ok=True)
plugins=a.build/'phase06-plugins'
ric_config=Path(os.environ.get('FLEXRIC_CONFIG',ROOT/'integration/config/flexric-local.conf'))
procs=[]; handles=[]
def start(name,cmd):
    f=(a.logs/name).open('w'); handles.append(f)
    q=subprocess.Popen(list(map(str,cmd)),cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
    procs.append(q); return q
def stop(q):
    if q and q.poll() is None:
        q.send_signal(signal.SIGINT)
        try: q.wait(timeout=8)
        except subprocess.TimeoutExpired: q.terminate(); q.wait(timeout=5)
def read(name):
    q=a.logs/name
    return q.read_text(errors='replace') if q.exists() else ''
try:
    with socket.socket(socket.AF_INET,socket.SOCK_SEQPACKET,socket.IPPROTO_SCTP) as s: s.bind(('127.0.0.1',36423))
    ric=start('ric.log',['stdbuf','-oL','-eL',a.ric,'-c',ric_config,'-p',str(plugins)+'/','-a','127.0.0.1'])
    ready=False
    for _ in range(160):
        if ric.poll() is not None: raise RuntimeError('RIC exited before SCTP listener became ready')
        lines=Path('/proc/net/sctp/eps').read_text().splitlines()
        if lines:
            ix=lines[0].split().index('LPORT')
            ready=any(int(line.split()[ix])==36421 for line in lines[1:])
        if ready: break
        time.sleep(.05)
    if not ready: raise RuntimeError('RIC SCTP listener did not start')
    native=start('native-lena.log',['stdbuf','-oL','-eL',a.build/'native-measurements','--gnbs=2','--ues=4',
        '--sampleMs=500','--duration=3','--csv='+str(a.logs/'measurements.csv'),'--enableE2=1',
        '--failE2GnbIndex=1','--allowPartialE2=1','--realtime=1','--ricIp=127.0.0.1','--ricPort=36421','--plugins='+str(a.build/'phase02-plugins')])
    try: native.wait(timeout=45)
    except subprocess.TimeoutExpired: stop(native); raise RuntimeError('native isolation scenario timed out')
    if native.returncode: raise RuntimeError(f'native isolation scenario exited {native.returncode}')
    stop(ric)
finally:
    for q in reversed(procs): stop(q)
    for f in handles: f.close()
native_log=read('native-lena.log'); ric_log=read('ric.log')
assert 'gNB_ID=101' in native_log and 'gNB_ID=102' in native_log
assert 'state=2 E2 Setup succeeded' in native_log
assert 'gnb_id=102 isolated state=' in native_log
assert 'E2 SETUP-REQUEST rx from PLMN 50501 Node ID 101' in ric_log
assert 'Node ID 102' not in ric_log
assert 'PASS native multi-gNB DL/UL measurement scenario' in native_log
print('PASS E2 failure isolation: gNB 102 connection refused; gNB 101 registered and radio simulation completed independently')
