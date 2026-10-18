#!/usr/bin/env python3
"""Exercise dashboard CSV-to-chart mapping using provider-shaped records."""
import csv
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
from server import Dashboard

header = ["sequence", "begin_sim_ns", "end_sim_ns", "gnb_id", "simulation_node_id", "cell_id", "bwp_index",
          "scope", "imsi", "rnti", "generation", "active_ues", "connected_ues", "available_prbs",
          "symbols_per_slot", "gnb_pdcp_attached", "ue_pdcp_attached", "unmapped_events", "dl_cqi_sum", "dl_cqi_samples"]
for direction in ("dl", "ul"):
    header += [f"{direction}_allocated_prb_grant_sum", f"{direction}_mcs_sum", f"{direction}_mcs_samples",
               f"{direction}_sinr_linear_sum", f"{direction}_sinr_samples", f"{direction}_pdcp_delay_mean_ns",
               f"{direction}_rlc_delay_mean_ns", f"{direction}_delivered_sdu_throughput_mbps",
               f"{direction}_rx_pdcp_packets"]

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "measurements.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for scope, imsi, gnb_id in (("cell", "", 101), ("cell", "", 102), ("cell", "", 103), ("ue", "501", 102)):
            row = {key: "" for key in header}
            row.update(sequence="1", begin_sim_ns="1000000000", end_sim_ns="2000000000", gnb_id=str(gnb_id),
                       simulation_node_id="1", cell_id="2", bwp_index="0", scope=scope, imsi=imsi,
                       rnti="7" if imsi else "", active_ues="1", connected_ues="1", dl_cqi_sum="12",
                       dl_cqi_samples="2")
            for direction in ("dl", "ul"):
                row.update({f"{direction}_allocated_prb_grant_sum": "25", f"{direction}_mcs_sum": "16",
                            f"{direction}_mcs_samples": "2", f"{direction}_sinr_linear_sum": "20",
                            f"{direction}_sinr_samples": "2", f"{direction}_pdcp_delay_mean_ns": "2500000",
                            f"{direction}_rlc_delay_mean_ns": "1000000", f"{direction}_delivered_sdu_throughput_mbps": "3.5",
                            f"{direction}_rx_pdcp_packets": "4"})
            writer.writerow(row)

    dashboard = Dashboard()
    dashboard.applied = {"runDirectory": temp, "values": {"csv": "measurements.csv"}}
    response = dashboard.measurements(["dl_throughput", "dl_mcs", "dl_sinr", "dl_pdcp_delay", "active_ues"], "cell", None)
    assert len(response["series"]) == 15, response
    assert {entry["gnbId"] for entry in response["series"]} == {101, 102, 103}, response
    by_metric = {entry["metric"]: entry["value"] for entry in response["series"]}
    assert by_metric == {"dl_throughput": 3.5, "dl_mcs": 8.0, "dl_sinr": 10.0, "dl_pdcp_delay": 2.5,
                         "active_ues": 1.0}, by_metric
    repeated = dashboard.measurements(["dl_throughput", "dl_mcs", "dl_sinr", "dl_pdcp_delay", "active_ues"], "cell", None)
    assert len(repeated["series"]) == len(response["series"]), repeated  # polling must not duplicate cached samples
    next_row = dict(row)
    next_row.update(sequence="2", end_sim_ns="3000000000", gnb_id="101", scope="cell", imsi="", rnti="",
                    dl_delivered_sdu_throughput_mbps="4.5")
    with path.open("a", newline="") as handle:
        csv.DictWriter(handle, fieldnames=header).writerow(next_row)
    appended = dashboard.measurements(["dl_throughput"], "cell", 101)
    assert [entry["value"] for entry in appended["series"]] == [3.5, 4.5], appended
    ue = dashboard.measurements(["dl_throughput"], "ue", 102)
    assert len(ue["series"]) == 1 and ue["series"][0]["series"] == "gNB 102 · UE 501", ue
    selected_gnb = dashboard.measurements(["dl_throughput"], "cell", 103)
    assert len(selected_gnb["series"]) == 1 and selected_gnb["series"][0]["gnbId"] == 103, selected_gnb
    missing = dashboard.measurements(["active_ues"], "ue", None)
    assert missing["series"] == [], missing

print("PASS dashboard measurement scope and derived-value mapping")
