#!/usr/bin/env python3
"""Run multi-gNB native LENA, original FlexRIC, KPM monitoring and private control."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser()
p.add_argument('--gnbs',type=int,choices=(2,3),default=2)
p.add_argument('--ues-per-gnb',type=int,default=2)
p.add_argument('--duration',type=int,default=8)
p.add_argument('--logs',type=Path,default=ROOT/'integration/build/phase06-validation')
p.add_argument('--build',type=Path,default=ROOT/'integration/build')
p.add_argument('--ric',type=Path,default=ROOT/'platforms/flexric/build/examples/ric/nearRT-RIC')
p.add_argument('--monitor',type=Path,default=ROOT/'platforms/flexric/build/examples/xApp/c/monitor/xapp_kpm_moni')
a=p.parse_args()
ric_config=Path(os.environ.get('FLEXRIC_CONFIG',ROOT/'integration/config/flexric-local.conf'))
if a.ues_per_gnb<2 or a.duration<8: raise SystemExit('phase06 requires >=2 UEs/gNB and >=8 simulation seconds')
a.logs.mkdir(parents=True,exist_ok=True)
plugins=a.build/'phase06-plugins'

for port in (36421,36422):
    with socket.socket(socket.AF_INET,socket.SOCK_SEQPACKET,socket.IPPROTO_SCTP) as probe:
        probe.bind(('127.0.0.1',port))

procs=[]; handles=[]
def start(name,cmd,env=None):
    print('COMMAND:',' '.join(map(str,cmd)),flush=True)
    f=(a.logs/name).open('w'); handles.append(f)
    proc=subprocess.Popen(list(map(str,cmd)),cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT)
    procs.append(proc); return proc
def stop(proc,sig=signal.SIGINT):
    if proc and proc.poll() is None:
        proc.send_signal(sig)
        try: proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
def read(path): return path.read_text(errors='replace') if path.exists() else ''

try:
    ric=start('ric.log',['stdbuf','-oL','-eL',a.ric,'-c',ric_config,'-p',str(plugins)+'/','-a','127.0.0.1'])
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if ric.poll() is not None: raise RuntimeError(f'RIC exited {ric.returncode}')
        eps=Path('/proc/net/sctp/eps').read_text().splitlines()
        if eps:
            idx=eps[0].split().index('LPORT')
            if any(int(line.split()[idx])==36421 for line in eps[1:]): break
        time.sleep(.05)
    else: raise RuntimeError('FlexRIC SCTP listener not ready')

    ue_count=a.gnbs*a.ues_per_gnb
    native=start('native-lena.log',['stdbuf','-oL','-eL',a.build/'native-measurements',f'--gnbs={a.gnbs}',f'--ues={ue_count}',
        '--sampleMs=1000',f'--duration={a.duration}',f'--csv={a.logs/"measurements.csv"}',
        '--enableE2=1','--enableControl=1','--realtime=1','--ricIp=127.0.0.1','--ricPort=36421',f'--plugins={plugins}'])
    native_started=time.monotonic()
    first_setup_wall=None
    all_setup_wall=None
    cpu_ticks=os.sysconf('SC_CLK_TCK')
    peak_rss={}
    cpu_seconds={}
    expected=a.gnbs
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if native.poll() is not None: raise RuntimeError(f'native exited before setup completion ({native.returncode})')
        text=read(a.logs/'native-lena.log')
        setup_count=text.count('state=2 E2 Setup succeeded')
        if setup_count and first_setup_wall is None: first_setup_wall=time.monotonic()
        if setup_count>=expected:
            all_setup_wall=time.monotonic()
            break
        time.sleep(.1)
    else: raise RuntimeError('not all native gNBs completed E2 Setup')

    monitor_env=os.environ.copy(); monitor_env['XAPP_DURATION']='45'
    monitor=start('xapp-kpm-monitor.log',['stdbuf','-oL','-eL',a.monitor,'-p',str(plugins)+'/'],monitor_env)
    control_env=os.environ.copy(); control_env['HERO_TARGET_GNB']='102'; control_env['HERO_MAX_DL_MCS']='1'
    control=start('xapp-hero-control.log',['stdbuf','-oL','-eL',a.build/'xapp-hero-control','-p',str(plugins)+'/'],control_env)
    assoc=[]; monitor_stopped=False
    deadline=time.monotonic()+max(120,a.duration*30+30)
    while native.poll() is None and time.monotonic()<deadline:
        if monitor.poll() is not None and not monitor_stopped:
            stop(native,signal.SIGTERM)
            raise RuntimeError(f'KPM monitor exited before enough indications ({monitor.returncode})')
        if control.poll() is not None and control.returncode!=0:
            stop(native,signal.SIGTERM)
            raise RuntimeError(f'control xApp exited before native scenario completed ({control.returncode})')
        assoc.append(Path('/proc/net/sctp/assocs').read_text())
        for name,proc in [('native',native),('ric',ric),('kpm_xapp',monitor),('control_xapp',control)]:
            if proc.poll() is not None: continue
            try:
                stat=Path(f'/proc/{proc.pid}/stat').read_text().split()
                cpu_seconds[name]=(int(stat[13])+int(stat[14]))/cpu_ticks
                status=Path(f'/proc/{proc.pid}/status').read_text()
                rss=int(re.search(r'^VmRSS:\s+(\d+)\s+kB',status,re.M).group(1))
                peak_rss[name]=max(peak_rss.get(name,0),rss)
            except (FileNotFoundError,AttributeError,IndexError): pass
        xlog=read(a.logs/'xapp-kpm-monitor.log')
        if not monitor_stopped and xlog.count('KPM ind_msg latency')>=2*a.gnbs:
            stop(monitor); monitor_stopped=True
        time.sleep(.1)
    native_finished=time.monotonic()
    if native.poll() is None:
        stop(native,signal.SIGTERM); raise RuntimeError('native scenario timed out')
    if native.returncode!=0: raise RuntimeError(f'native scenario exited {native.returncode}')
    if control.poll() is None: stop(control)
    if control.returncode!=0: raise RuntimeError(f'control xApp exited {control.returncode}')
    if not monitor_stopped: raise RuntimeError('monitor did not decode expected KPM indications')
    if monitor.returncode!=0: raise RuntimeError(f'monitor xApp exited {monitor.returncode}')
    (a.logs/'sctp-associations.log').write_text('\n'.join(assoc))
    native_log_for_metrics=read(a.logs/'native-lena.log')
    monitor_log_for_metrics=read(a.logs/'xapp-kpm-monitor.log')
    indications=native_log_for_metrics.count('[HERO KPM] encoded native sample')
    decoded_indications=monitor_log_for_metrics.count('KPM ind_msg latency')
    kpm_wire_sizes=[(int(node),int(size)) for node,size in re.findall(
        r'KPM_SCTP_USER_MESSAGE gnb_id=(\d+) e2ap_aper_bytes=(\d+)',native_log_for_metrics)]
    sizes_by_gnb={str(gnb):[size for node,size in kpm_wire_sizes if node==gnb] for gnb in range(101,101+a.gnbs)}
    (a.logs/'overhead.json').write_text(json.dumps({
        'gnbs':a.gnbs,'ues':ue_count,'simulated_seconds':a.duration,
        'native_wall_seconds':round(native_finished-native_started,3),
        'wall_seconds_per_simulated_second':round((native_finished-native_started)/a.duration,3),
        'time_to_first_setup_response_seconds':None if first_setup_wall is None else round(first_setup_wall-native_started,3),
        'time_to_all_setup_responses_seconds':None if all_setup_wall is None else round(all_setup_wall-native_started,3),
        'native_kpm_samples_encoded':indications,'stock_xapp_indications_decoded':decoded_indications,
        'max_sampled_process_cpu_seconds':{k:round(v,3) for k,v in cpu_seconds.items()},
        'peak_sampled_process_rss_kb':peak_rss,
        'kpm_e2ap_aper_message_count':len(kpm_wire_sizes),
        'kpm_e2ap_aper_message_bytes_per_gnb':sizes_by_gnb,
        'kpm_e2ap_aper_message_bytes_total':sum(size for _,size in kpm_wire_sizes),
        'kpm_e2ap_aper_message_bytes_mean':None if not kpm_wire_sizes else round(sum(size for _,size in kpm_wire_sizes)/len(kpm_wire_sizes),1),
        'message_size_scope':'Exact encoded E2AP APER SCTP user-message bytes, observed by a link wrapper around original FlexRIC e2ap_send_sctp_msg; excludes SCTP/IP framing and retransmission bytes.',
        'sampling_note':'CPU and RSS are sampled from /proc every ~100 ms; short peaks may be missed.'
    },indent=2)+'\n')
    stop(ric)
finally:
    for proc in reversed(procs): stop(proc)
    for f in handles: f.close()

native_log=read(a.logs/'native-lena.log'); control_log=read(a.logs/'xapp-hero-control.log')
monitor_log=read(a.logs/'xapp-kpm-monitor.log'); ric_log=read(a.logs/'ric.log')
print('--- NATIVE ---\n'+native_log)
print('--- CONTROL XAPP ---\n'+control_log)
print('--- KPM XAPP ---\n'+monitor_log)
print('--- RIC ---\n'+ric_log)
for gnb in range(101,101+a.gnbs):
    assert f'gNB_ID={gnb}' in native_log
    assert f'[HERO KPM] encoded native sample' in native_log and f'gnb_id={gnb}' in native_log
    assert f'[HERO E2] E2_SETUP_RESPONSE accepted RAN function ID=2' in native_log
wire_sizes=[(int(node),int(size)) for node,size in re.findall(
    r'KPM_SCTP_USER_MESSAGE gnb_id=(\d+) e2ap_aper_bytes=(\d+)',native_log)]
native_indications=native_log.count('[HERO KPM] encoded native sample')
decoded_indications=monitor_log.count('KPM ind_msg latency')
assert len(wire_sizes)==native_indications and decoded_indications==native_indications
assert all(any(node==gnb and size>0 for node,size in wire_sizes) for gnb in range(101,101+a.gnbs))
assert f'registered_nodes={a.gnbs}' in control_log
assert 'target_gnb=102' in control_log and 'CONTROL_RESPONSE target_gnb=102' in control_log
assert len(re.findall(r'operation=SetMaxDlMcs requested=1 applied=1 effective=1',native_log))==1
assert native_log.count('subscription admitted')>=a.gnbs
assert 'PASS native multi-gNB DL/UL measurement scenario' in native_log

rows=list(csv.DictReader((a.logs/'measurements.csv').open()))
cells={int(r['gnb_id']):r for r in rows if r['scope']=='cell'}
assert set(cells)==set(range(101,101+a.gnbs)),f'CSV node identities: {set(cells)}'
ue_by_node={g:set() for g in cells}
for r in rows:
    g=int(r['gnb_id'])
    if r['scope']=='ue':
        assert int(r['cell_id'])==int(cells[g]['cell_id'])
        ue_by_node[g].add(int(r['imsi']))
assert all(len(ues)>=a.ues_per_gnb for ues in ue_by_node.values()),ue_by_node
control_match=re.search(r'sim_time_ns=(\d+)',native_log)
assert control_match,'No simulation-thread control execution timestamp'
control_time=int(control_match.group(1))
target_rows=[r for r in rows if int(r['gnb_id'])==102 and r['scope']=='cell' and int(r['begin_sim_ns'])>=control_time and int(r['dl_mcs_samples'])>0]
other_rows=[r for r in rows if int(r['gnb_id'])!=102 and r['scope']=='cell' and int(r['begin_sim_ns'])>=control_time and int(r['dl_mcs_samples'])>0]
assert target_rows and all(int(r['dl_mcs_sum'])/int(r['dl_mcs_samples'])<=1.0 for r in target_rows), 'Target gNB did not show the requested DL MCS cap'
assert other_rows and any(int(r['dl_mcs_sum'])/int(r['dl_mcs_samples'])>1.0 for r in other_rows), 'Non-target gNB did not retain its independent scheduler setting'
print(f'PASS phase 06: {a.gnbs} native E2 nodes; per-node KPM and UE contexts; gNB 102 MaxDlMcs=1 applied at sim_ns={control_time}; other gNBs unchanged')
print('OVERHEAD:',(a.logs/'overhead.json').read_text())
