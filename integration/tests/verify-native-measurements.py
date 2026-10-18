#!/usr/bin/env python3
"""Verify measurements using real native traffic, exact counters and repeated runs."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

root=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser()
parser.add_argument('--binary',type=Path,default=root/'integration/build/native-measurements')
parser.add_argument('--output',type=Path,default=root/'integration/logs/phase03/verified')
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=True)

def execute(name,*options):
    path=args.output/(name+'.csv')
    command=[str(args.binary),'--csv='+str(path),*options]
    result=subprocess.run(command,cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
    (args.output/(name+'.log')).write_text('COMMAND: '+' '.join(command)+'\n'+result.stdout)
    print(result.stdout,flush=True)
    assert result.returncode==0,(name,result.returncode)
    return path,result.stdout

runs=[]
for name in ('run-a','run-b'):
    path,output=execute(name)
    rows=list(csv.DictReader(path.open()))
    assert len(rows)==80
    groups={}
    for row in rows:
        groups.setdefault(int(row['sequence']),[]).append(row)
        assert int(row['gnb_id'])==101 and int(row['cell_id'])==1 and int(row['bwp_index'])==0
        assert int(row['unmapped_events'])==0
    additive=[k for k in rows[0] if k.startswith(('dl_','ul_')) and '_slice_' not in k and not k.endswith(('throughput_mbps','delay_mean_ns')) and not k.startswith('ul_sinr')]
    cells=[]
    for sequence,group in sorted(groups.items()):
        cell=[r for r in group if r['scope']=='cell'][0]
        ues=[r for r in group if r['scope']=='ue']
        assert sorted(int(r['imsi']) for r in ues)==[1,2,3]
        assert sorted(int(r['rnti']) for r in ues)==[1,2,3]
        assert len({r['generation'] for r in ues})==1
        assert int(cell['begin_sim_ns'])==(sequence-1)*100_000_000
        assert int(cell['end_sim_ns'])==sequence*100_000_000
        assert int(cell['active_ues'])==sum(int(r['active_ues']) for r in ues)
        assert int(cell['connected_ues'])==sum(int(r['connected_ues']) for r in ues)
        for key in additive:
            assert math.isclose(float(cell[key]),sum(float(r[key]) for r in ues),rel_tol=1e-12,abs_tol=1e-9),(sequence,key)
        for r in group:
            for direction in ('dl','ul'):
                packets=int(r[f'{direction}_rx_pdcp_packets'])
                assert int(r[f'{direction}_rx_pdcp_pdu_bytes'])-int(r[f'{direction}_rx_pdcp_sdu_bytes'])==2*packets
                seconds=(int(r['end_sim_ns'])-int(r['begin_sim_ns']))*1e-9
                throughput=r[f'{direction}_delivered_sdu_throughput_mbps']
                if throughput:
                    assert math.isclose(float(throughput),int(r[f'{direction}_rx_pdcp_sdu_bytes'])*8e-6/seconds,abs_tol=1e-12)
                else:
                    assert int(r[f'{direction}_rx_pdcp_sdu_bytes'])==0
                grants=int(r[f'{direction}_data_grants'])
                prbs=int(r[f'{direction}_allocated_prb_grant_sum'])
                area=int(r[f'{direction}_allocated_prb_symbols'])
                assert prbs<=int(r['available_prbs'])*grants
                assert prbs<=area<=prbs*int(r['symbols_per_slot'])
                assert int(r[f'{direction}_mcs_samples'])==grants
                if packets:
                    assert math.isclose(float(r[f'{direction}_pdcp_delay_mean_ns']),int(r[f'{direction}_pdcp_rx_delay_ns_sum'])/packets,rel_tol=1e-12)
                else:
                    assert r[f'{direction}_pdcp_delay_mean_ns']==''
            if r['scope']=='ue':
                assert int(r['ul_sinr_samples'])==0 # source does not expose an attributable UE ID
        cells.append(cell)
    assert sum(int(r['dl_rx_pdcp_packets']) for r in cells)==300
    assert sum(int(r['ul_rx_pdcp_packets']) for r in cells)==240
    assert sum(int(r['dl_rx_pdcp_sdu_bytes']) for r in cells)==162000 # 300 * (512 + UDP8 + IPv4 20)
    assert sum(int(r['ul_rx_pdcp_sdu_bytes']) for r in cells)==68160 # 240 * (256 + UDP8 + IPv4 20)
    for key in ('dl_allocated_prb_grant_sum','ul_allocated_prb_grant_sum','dl_sinr_samples','ul_sinr_samples','dl_cqi_samples','dl_rx_rlc_packets','ul_rx_rlc_packets'):
        assert sum(int(r[key]) for r in cells)>0,key
    assert max(int(r['active_ues']) for r in cells)==3
    assert all(int(r['dl_rx_pdcp_packets'])==int(r['ul_rx_pdcp_packets'])==int(r['active_ues'])==0 for r in cells[-3:])
    runs.append((path,output,cells))
assert runs[0][0].read_bytes()==runs[1][0].read_bytes(),'Fixed-seed samples not byte-identical'
_,without=execute('observer-disabled','--collect=0')
pattern=r'UE imsi=\d+ serving_cell=\d+ rnti=\d+ DL_UDP=\d+ UL_UDP=\d+'
assert re.findall(pattern,runs[0][1])==re.findall(pattern,without),'Observer changed application delivery'
# Non-divisible sampling interval checks terminal partial-window flush and bytes.
partial,_=execute('partial-window','--sampleMs=137')
partial_rows=[r for r in csv.DictReader(partial.open()) if r['scope']=='cell']
assert int(partial_rows[-1]['end_sim_ns'])==2_000_000_000
assert int(partial_rows[-1]['end_sim_ns'])-int(partial_rows[-1]['begin_sim_ns'])==82_000_000
assert sum(int(r['dl_rx_pdcp_sdu_bytes']) for r in partial_rows)==162000
assert sum(int(r['ul_rx_pdcp_sdu_bytes']) for r in partial_rows)==68160
summary={
 'result':'PASS','default_windows':len(runs[0][2]),'default_csv_rows':80,'ues':3,
 'dl_udp_delivered':300,'ul_udp_delivered':240,
 'dl_pdcp_delivered_sdu_bytes':162000,'ul_pdcp_delivered_sdu_bytes':68160,
 'dl_allocated_prb_grant_sum':sum(int(r['dl_allocated_prb_grant_sum']) for r in runs[0][2]),
 'ul_allocated_prb_grant_sum':sum(int(r['ul_allocated_prb_grant_sum']) for r in runs[0][2]),
 'csv_sha256':hashlib.sha256(runs[0][0].read_bytes()).hexdigest(),
 'byte_identical_repeat':True,'observer_disabled_delivery_unchanged':True,'partial_final_window_ns':82_000_000,
}
(args.output/'verification.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
