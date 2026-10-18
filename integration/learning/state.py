"""Fixed-order, provenance-aware local and regional HERO observation schemas."""
from __future__ import annotations

from dataclasses import dataclass
import csv
import math
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np


# Five categories from the supplied HERO model. A value followed by its own
# availability bit is emitted for every feature, so missing telemetry is not 0.
FEATURES: tuple[tuple[str, str, str], ...] = (
    ("PHY", "dl_sinr_db", "dB"),
    ("PHY", "ul_sinr_linear", "linear ratio"),
    ("PHY", "dl_cqi_mean", "CQI index"),
    ("PHY", "dl_mcs_mean", "MCS index"),
    ("PHY", "ul_mcs_mean", "MCS index"),
    ("PHY", "bler", "ratio; unavailable in current provider"),
    ("PHY", "harq", "ratio/count; unavailable in current provider"),
    ("LOAD", "active_ues", "UEs scheduled in window"),
    ("LOAD", "connected_ues", "UEs at sample end"),
    ("LOAD", "delivered_dl_mbps", "Mbit/s; delivered PDCP SDUs"),
    ("LOAD", "delivered_ul_mbps", "Mbit/s; delivered PDCP SDUs"),
    ("LOAD", "queue_bytes", "bytes; unavailable in current provider"),
    ("QOS", "dl_throughput_mbps", "Mbit/s; PDCP SDU throughput"),
    ("QOS", "ul_throughput_mbps", "Mbit/s; PDCP SDU throughput"),
    ("QOS", "dl_pdcp_delay_ms", "ms; mean PDCP trace delay"),
    ("QOS", "ul_pdcp_delay_ms", "ms; mean PDCP trace delay"),
    ("QOS", "packet_loss_ratio", "ratio; unavailable in current provider"),
    ("RES", "dl_allocated_prb_grants", "PRB grant count per sample window"),
    ("RES", "ul_allocated_prb_grants", "PRB grant count per sample window"),
    ("RES", "dl_allocated_prb_symbols", "PRB-symbol count per sample window"),
    ("RES", "ul_allocated_prb_symbols", "PRB-symbol count per sample window"),
    ("RES", "available_prbs", "configured BWP PRBs; not utilization"),
    ("RES", "previous_embb_ratio", "ratio from last policy action"),
    ("RES", "previous_urllc_ratio", "ratio from last policy action"),
    ("RES", "previous_mmtc_ratio", "ratio from last policy action"),
    ("SLA", "embb_throughput_gap", "fraction of configured DL minimum unmet"),
    ("SLA", "urllc_throughput_gap", "fraction of configured DL minimum unmet"),
    ("SLA", "mmtc_throughput_gap", "fraction of configured DL minimum unmet"),
    ("SLA", "embb_throughput_violation", "0/1 among evaluated UEs"),
    ("SLA", "urllc_throughput_violation", "0/1 among evaluated UEs"),
    ("SLA", "mmtc_throughput_violation", "0/1 among evaluated UEs"),
)
FEATURE_NAMES = tuple(f[1] for f in FEATURES)
OBSERVATION_DIM = len(FEATURES) * 2
UE_AWARE_FEATURES: tuple[str, ...] = (
    "connected_ues", "offered_bps", "required_bps", "estimated_demand_units",
    "mean_ue_demand_units", "max_ue_demand_units", "demand_share", "demand_dispersion",
    "dl_goodput_bps", "ul_goodput_bps",
    "dl_delay_ms", "mean_sinr_db", "mean_cqi", "mean_mcs", "previous_ratio",
    "applied_budget_share", "mac_used_share", "unused_budget_share",
    "qos_satisfaction", "worst_ue_satisfaction", "satisfied_ue_fraction",
    "throughput_deficit", "delay_violation", "packet_loss_violation",
)
UE_AWARE_OBSERVATION_DIM = len(UE_AWARE_FEATURES) * 3 * 2
DEFAULT_SCALES: Mapping[str, float] = {
    "dl_sinr_db": 40.0, "ul_sinr_linear": 100.0, "dl_cqi_mean": 15.0,
    "dl_mcs_mean": 28.0, "ul_mcs_mean": 28.0, "bler": 1.0, "harq": 1.0,
    "active_ues": 32.0, "connected_ues": 32.0,
    "delivered_dl_mbps": 100.0, "delivered_ul_mbps": 100.0,
    "queue_bytes": 1_000_000.0, "dl_throughput_mbps": 100.0,
    "ul_throughput_mbps": 100.0, "dl_pdcp_delay_ms": 1000.0,
    "ul_pdcp_delay_ms": 1000.0, "packet_loss_ratio": 1.0,
    "dl_allocated_prb_grants": 100_000.0, "ul_allocated_prb_grants": 100_000.0,
    "dl_allocated_prb_symbols": 1_000_000.0, "ul_allocated_prb_symbols": 1_000_000.0,
    "available_prbs": 275.0, "previous_embb_ratio": 1.0,
    "previous_urllc_ratio": 1.0, "previous_mmtc_ratio": 1.0,
    "embb_throughput_gap": 1.0, "urllc_throughput_gap": 1.0,
    "mmtc_throughput_gap": 1.0, "embb_throughput_violation": 1.0,
    "urllc_throughput_violation": 1.0, "mmtc_throughput_violation": 1.0,
}


@dataclass(frozen=True)
class LocalObservation:
    gnb_id: int
    sequence: int
    begin_sim_ns: int
    end_sim_ns: int
    values: tuple[float, ...]
    diagnostics: Mapping[str, object] | None = None


@dataclass(frozen=True)
class RegionalObservation:
    # Stable sorted gNB identity order is external to the learned vector and
    # stored in every transition, avoiding lossy numeric gNB-ID embeddings.
    gnb_ids: tuple[int, ...]
    sequence: int
    begin_sim_ns: int
    end_sim_ns: int
    values: tuple[float, ...]


def _number(row: Mapping[str, str], key: str) -> float | None:
    raw = row.get(key, "")
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _mean(row: Mapping[str, str], total: str, samples: str) -> float | None:
    n, d = _number(row, total), _number(row, samples)
    return n / d if n is not None and d is not None and d > 0 else None


class HeroStateBuilder:
    """Build local observations from actual integration measurement CSV rows."""

    def __init__(self, scales: Mapping[str, float] | None = None):
        self.scales = dict(DEFAULT_SCALES if scales is None else scales)
        missing = set(FEATURE_NAMES) - self.scales.keys()
        if missing or any(not math.isfinite(float(v)) or float(v) <= 0 for v in self.scales.values()):
            raise ValueError(f"all feature scales must be positive and finite; missing {sorted(missing)}")

    def build_local(self, rows: Sequence[Mapping[str, str]],
                    previous_ratios: Sequence[float] | None = None) -> LocalObservation:
        cells = [r for r in rows if r.get("scope") == "cell"]
        if len(cells) != 1:
            raise ValueError("local observation requires exactly one cell row")
        cell = cells[0]
        gnb_id, sequence = int(cell["gnb_id"]), int(cell["sequence"])
        if any(int(r["gnb_id"]) != gnb_id or int(r["sequence"]) != sequence for r in rows):
            raise ValueError("local observation rows must match gNB and sample sequence")
        previous = tuple(previous_ratios) if previous_ratios is not None else (None, None, None)
        if len(previous) != 3 or (all(v is not None for v in previous) and
                                   (any(not math.isfinite(float(v)) or float(v) < 0 for v in previous)
                                    or not math.isclose(sum(previous), 1.0, abs_tol=1e-5))):
            raise ValueError("previous ratios must be absent or a normalized three-slice vector")
        dl_sinr = _mean(cell, "dl_sinr_linear_sum", "dl_sinr_samples")
        if dl_sinr is not None:
            dl_sinr = 10.0 * math.log10(dl_sinr) if dl_sinr > 0 else None
        values: dict[str, float | None] = {
            "dl_sinr_db": dl_sinr,
            "ul_sinr_linear": _mean(cell, "ul_sinr_linear_sum", "ul_sinr_samples"),
            "dl_cqi_mean": _mean(cell, "dl_cqi_sum", "dl_cqi_samples"),
            "dl_mcs_mean": _mean(cell, "dl_mcs_sum", "dl_mcs_samples"),
            "ul_mcs_mean": _mean(cell, "ul_mcs_sum", "ul_mcs_samples"),
            "bler": None, "harq": None,
            "active_ues": _number(cell, "active_ues"),
            "connected_ues": _number(cell, "connected_ues"),
            "delivered_dl_mbps": _number(cell, "dl_delivered_sdu_throughput_mbps"),
            "delivered_ul_mbps": _number(cell, "ul_delivered_sdu_throughput_mbps"),
            "queue_bytes": None,
            "dl_throughput_mbps": _number(cell, "dl_delivered_sdu_throughput_mbps"),
            "ul_throughput_mbps": _number(cell, "ul_delivered_sdu_throughput_mbps"),
            "dl_pdcp_delay_ms": ((v / 1e6) if (v := _number(cell, "dl_pdcp_delay_mean_ns")) is not None else None),
            "ul_pdcp_delay_ms": ((v / 1e6) if (v := _number(cell, "ul_pdcp_delay_mean_ns")) is not None else None),
            "packet_loss_ratio": None,
            "dl_allocated_prb_grants": _number(cell, "dl_allocated_prb_grant_sum"),
            "ul_allocated_prb_grants": _number(cell, "ul_allocated_prb_grant_sum"),
            "dl_allocated_prb_symbols": _number(cell, "dl_allocated_prb_symbols"),
            "ul_allocated_prb_symbols": _number(cell, "ul_allocated_prb_symbols"),
            "available_prbs": _number(cell, "available_prbs"),
            "previous_embb_ratio": previous[0], "previous_urllc_ratio": previous[1],
            "previous_mmtc_ratio": previous[2],
        }
        slice_ids = {"embb": 1, "urllc": 2, "mmtc": 3}
        ue_rows = [r for r in rows if r.get("scope") == "ue"]
        for name, slice_id in slice_ids.items():
            targets = []
            achieved = []
            for row in ue_rows:
                if int(_number(row, "slice_id") or 0) != slice_id:
                    continue
                target = _number(row, "required_throughput_bps")
                measured = _number(row, "dl_delivered_sdu_throughput_mbps")
                if target is not None and target > 0 and measured is not None:
                    targets.append(target)
                    achieved.append(measured * 1e6)
            gap_key, violation_key = f"{name}_throughput_gap", f"{name}_throughput_violation"
            if targets:
                values[gap_key] = max(0.0, (sum(targets) - sum(achieved)) / sum(targets))
                values[violation_key] = float(any(a < t for a, t in zip(achieved, targets)))
            else:
                values[gap_key] = None
                values[violation_key] = None
        normalized: list[float] = []
        for feature in FEATURE_NAMES:
            value = values.get(feature)
            if value is None or not math.isfinite(float(value)):
                normalized.extend((0.0, 0.0))
            else:
                normalized.extend((float(value) / self.scales[feature], 1.0))
        return LocalObservation(gnb_id, sequence, int(cell["begin_sim_ns"]),
                                int(cell["end_sim_ns"]), tuple(normalized))

    def build_local_ue_aware(self, rows: Sequence[Mapping[str, str]],
                             previous_ratios: Sequence[float] | None = None) -> LocalObservation:
        """Build a fixed-size, per-slice state from measured UE and scheduler rows.

        Demand is estimated in DL RBG-symbol opportunities. Configured minimum
        rate is used when positive, otherwise offered traffic rate is an explicit
        proxy. Queue backlog/deadline bytes are not exposed by this LENA provider.
        """
        cells = [r for r in rows if r.get("scope") == "cell"]
        ue_rows = [r for r in rows if r.get("scope") == "ue"]
        if len(cells) != 1:
            raise ValueError("UE-aware local observation requires exactly one cell row")
        cell = cells[0]
        gnb_id, sequence = int(cell["gnb_id"]), int(cell["sequence"])
        if any(int(r["gnb_id"]) != gnb_id or int(r["sequence"]) != sequence for r in rows):
            raise ValueError("local UE-aware rows must match gNB and sample sequence")
        previous = tuple(previous_ratios) if previous_ratios is not None else (None, None, None)
        if len(previous) != 3 or (all(v is not None for v in previous) and
                (any(not math.isfinite(float(v)) or float(v) < 0 for v in previous) or
                 not math.isclose(sum(previous), 1.0, abs_tol=1e-5))):
            raise ValueError("previous ratios must be absent or a normalized three-slice vector")
        duration_s = max(0.0, (int(cell["end_sim_ns"]) - int(cell["begin_sim_ns"])) / 1e9)
        slices = {1: "embb", 2: "urllc", 3: "mmtc"}
        budgets = []
        uses = []
        for slice_name in slices.values():
            budgets.append(_number(cell, f"dl_slice_budget_{slice_name}"))
            uses.append(_number(cell, f"dl_slice_used_{slice_name}"))
        total_budget = sum(v for v in budgets if v is not None)
        total_use = sum(v for v in uses if v is not None)
        demand_capacity = total_budget if total_budget > 0 else None
        feature_rows: list[float] = []
        diagnostics: dict[str, object] = {"demand_unit": "DL RBG-symbol opportunities",
            "demand_proxy": "max(configured required throughput, offered rate) × window; urgent queue bytes unavailable",
            "slices": {}}

        def bounded_log(value: float) -> float:
            if not math.isfinite(value) or value < 0:
                raise ValueError("log-normalized feature must be finite and nonnegative")
            return min(1.0, math.log1p(value) / math.log1p(1e9))

        for index, (slice_id, slice_name) in enumerate(slices.items()):
            # Experimental per-slice QoS mix; packet-loss weight remains zero
            # until native packet-loss telemetry exists.
            alpha, beta = {1: (0.7, 0.3), 2: (0.3, 0.7), 3: (0.8, 0.2)}[slice_id]
            members = [r for r in ue_rows if int(_number(r, "slice_id") or 0) == slice_id
                       and (_number(r, "connected_ues") or 0) > 0]
            offered = [max(0.0, _number(r, "offered_rate_bps") or 0.0) for r in members]
            required = [max(0.0, _number(r, "required_throughput_bps") or 0.0) for r in members]
            target_rates = [req if req > 0 else offer for req, offer in zip(required, offered)]
            dl_rates = [max(0.0, _number(r, "dl_delivered_sdu_throughput_mbps") or 0.0) * 1e6
                        for r in members]
            ul_rates = [max(0.0, _number(r, "ul_delivered_sdu_throughput_mbps") or 0.0) * 1e6
                        for r in members]
            dl_delays = [v / 1e6 for r in members
                         if (v := _number(r, "dl_pdcp_delay_mean_ns")) is not None and v >= 0]
            sinrs = [10.0 * math.log10(v) for r in members
                     if (v := _mean(r, "dl_sinr_linear_sum", "dl_sinr_samples")) is not None and v > 0]
            cqis = [_mean(r, "dl_cqi_sum", "dl_cqi_samples") for r in members]
            cqis = [v for v in cqis if v is not None]
            mcs = [_mean(r, "dl_mcs_sum", "dl_mcs_samples") for r in members]
            mcs = [v for v in mcs if v is not None]
            demand_values: list[float] = []
            demand_complete = True
            for row, target_bps in zip(members, target_rates):
                if target_bps <= 0 or duration_s <= 0:
                    demand_values.append(0.0)
                    continue
                delivered_bits = max(0.0, _number(row, "dl_rx_pdcp_sdu_bytes") or 0.0) * 8.0
                prb_symbols = max(0.0, _number(row, "dl_allocated_prb_symbols") or 0.0)
                prbs_per_rbg = _number(row, "prbs_per_rbg") or 0.0
                if delivered_bits <= 0 or prb_symbols <= 0 or prbs_per_rbg <= 0:
                    demand_complete = False
                    continue
                delivered_rbg_symbols = prb_symbols / prbs_per_rbg
                efficiency = delivered_bits / delivered_rbg_symbols
                if not math.isfinite(efficiency) or efficiency <= 0:
                    demand_complete = False
                    continue
                # Queue urgency cannot be read from the supported provider; use
                # configured required/offered rate over this completed interval.
                demand_values.append(target_bps * duration_s / efficiency)
            demand = sum(demand_values) if demand_complete else None
            budget, used = budgets[index], uses[index]
            budget_share = budget / total_budget if budget is not None and total_budget > 0 else None
            use_share = used / total_budget if used is not None and total_budget > 0 else None
            unused_share = (max(0.0, budget - used) / total_budget
                            if budget is not None and used is not None and total_budget > 0 else None)
            demand_share = demand / demand_capacity if demand is not None and demand_capacity else None
            dispersion = (float(np.std(demand_values) / (np.mean(demand_values) + 1e-9))
                          if demand_complete and demand_values else (0.0 if demand_complete else None))
            satisfaction: list[float] = []
            throughput_violations: list[float] = []
            delay_violations: list[float] = []
            ue_demand_units: list[float | None] = []
            for row, target_bps, achieved in zip(members, target_rates, dl_rates):
                components: list[tuple[float, float]] = []
                if target_bps > 0:
                    vt = min(1.0, max(0.0, 1.0 - achieved / target_bps))
                    throughput_violations.append(vt)
                    components.append((alpha, vt))
                target_ms = _number(row, "delay_target_ms") or 0.0
                delay_ns = _number(row, "dl_pdcp_delay_mean_ns")
                if target_ms > 0 and delay_ns is not None and delay_ns >= 0:
                    vd = min(1.0, max(0.0, (delay_ns / 1e6 - target_ms) / target_ms))
                    delay_violations.append(vd)
                    components.append((beta, vd))
                if components:
                    weight_sum = sum(weight for weight, _ in components)
                    satisfaction.append(min(1.0, max(0.0, 1.0 -
                        sum(weight * violation for weight, violation in components) / weight_sum)))
                delivered_bits = max(0.0, _number(row, "dl_rx_pdcp_sdu_bytes") or 0.0) * 8.0
                prb_symbols = max(0.0, _number(row, "dl_allocated_prb_symbols") or 0.0)
                prbs_per_rbg = _number(row, "prbs_per_rbg") or 0.0
                if target_bps <= 0:
                    ue_demand_units.append(0.0)
                elif delivered_bits > 0 and prb_symbols > 0 and prbs_per_rbg > 0 and duration_s > 0:
                    efficiency = delivered_bits / (prb_symbols / prbs_per_rbg)
                    ue_demand_units.append(target_bps * duration_s / efficiency
                                           if math.isfinite(efficiency) and efficiency > 0 else None)
                else:
                    ue_demand_units.append(None)
            qos_mean = float(np.mean(satisfaction)) if satisfaction else None
            qos_worst = min(satisfaction) if satisfaction else None
            sat_fraction = (sum(q >= 0.8 for q in satisfaction) / len(satisfaction)
                            if satisfaction else None)
            throughput_deficit = float(np.mean(throughput_violations)) if throughput_violations else None
            delay_violation = float(np.mean(delay_violations)) if delay_violations else None
            feature_map: dict[str, float | None] = {
                "connected_ues": float(len(members)),
                "offered_bps": sum(offered), "required_bps": sum(target_rates),
                "estimated_demand_units": demand,
                "mean_ue_demand_units": (float(np.mean(demand_values)) if demand_complete and demand_values else (0.0 if demand_complete else None)),
                "max_ue_demand_units": (max(demand_values) if demand_complete and demand_values else (0.0 if demand_complete else None)),
                "demand_share": demand_share, "demand_dispersion": dispersion,
                "dl_goodput_bps": sum(dl_rates), "ul_goodput_bps": sum(ul_rates),
                "dl_delay_ms": float(np.mean(dl_delays)) if dl_delays else None,
                "mean_sinr_db": float(np.mean(sinrs)) if sinrs else None,
                "mean_cqi": float(np.mean(cqis)) if cqis else None,
                "mean_mcs": float(np.mean(mcs)) if mcs else None,
                "previous_ratio": float(previous[index]) if previous[index] is not None else None,
                "applied_budget_share": budget_share, "mac_used_share": use_share,
                "unused_budget_share": unused_share,
                "qos_satisfaction": qos_mean, "worst_ue_satisfaction": qos_worst,
                "satisfied_ue_fraction": sat_fraction,
                "throughput_deficit": throughput_deficit, "delay_violation": delay_violation,
                "packet_loss_violation": None,
            }
            diagnostics["slices"][slice_name] = {
                "active_ue_count": len(members), "offered_bps": sum(offered),
                "required_bps": sum(target_rates), "estimated_demand_units": demand,
                "demand_complete": demand_complete, "actual_budget_units": budget,
                "actual_mac_used_units": used, "qos_satisfaction": qos_mean,
                "worst_ue_satisfaction": qos_worst, "satisfied_ue_fraction": sat_fraction,
                "throughput_violation": throughput_deficit,
                "delay_violation": delay_violation,
                "reliability_violation": None,
                "ue_satisfaction": satisfaction,
                "ue_demand_units": ue_demand_units,
            }
            for feature in UE_AWARE_FEATURES:
                value = feature_map[feature]
                if value is None or not math.isfinite(value):
                    feature_rows.extend((0.0, 0.0))
                else:
                    if feature in ("offered_bps", "required_bps", "dl_goodput_bps", "ul_goodput_bps"):
                        normalized = bounded_log(max(0.0, value))
                    elif feature == "connected_ues":
                        normalized = bounded_log(value)
                    elif feature in ("estimated_demand_units", "mean_ue_demand_units", "max_ue_demand_units"):
                        normalized = bounded_log(max(0.0, value))
                    elif feature == "demand_dispersion":
                        normalized = min(1.0, max(0.0, value) / (1.0 + max(0.0, value)))
                    elif feature == "dl_delay_ms":
                        normalized = bounded_log(max(0.0, value))
                    elif feature == "mean_sinr_db":
                        normalized = min(1.0, max(-1.0, value / 40.0))
                    elif feature == "mean_cqi":
                        normalized = min(1.0, max(0.0, value / 15.0))
                    elif feature == "mean_mcs":
                        normalized = min(1.0, max(0.0, value / 28.0))
                    elif feature in ("demand_share",):
                        normalized = min(1.0, max(0.0, value / (1.0 + value)))
                    elif feature in ("throughput_deficit", "delay_violation"):
                        normalized = min(1.0, max(0.0, value))
                    else:
                        normalized = min(1.0, max(0.0, value))
                    feature_rows.extend((normalized, 1.0))
        if len(feature_rows) != UE_AWARE_OBSERVATION_DIM or not np.isfinite(feature_rows).all():
            raise ValueError("UE-aware observation has invalid dimension or non-finite values")
        diagnostics["state_feature_names"] = [f"{slice_name}.{feature}" for slice_name in slices.values()
                                                for feature in UE_AWARE_FEATURES]
        diagnostics["applied_ratios"] = [_number(cell, f"applied_{name}_ratio") for name in slices.values()]
        diagnostics["window_seconds"] = duration_s
        diagnostics["capacity_units"] = total_budget if total_budget > 0 else None
        return LocalObservation(gnb_id, sequence, int(cell["begin_sim_ns"]),
                                int(cell["end_sim_ns"]), tuple(feature_rows), diagnostics)

    def build_regional(self, locals_: Sequence[LocalObservation], max_gnbs: int) -> RegionalObservation:
        if max_gnbs < 1 or not locals_ or len(locals_) > max_gnbs:
            raise ValueError("regional observation must contain 1..max_gnbs cells")
        ordered = sorted(locals_, key=lambda x: x.gnb_id)
        if len({x.gnb_id for x in ordered}) != len(ordered):
            raise ValueError("regional state contains duplicate gNB IDs")
        if len({x.sequence for x in ordered}) != 1 or len({x.begin_sim_ns for x in ordered}) != 1 or len({x.end_sim_ns for x in ordered}) != 1:
            raise ValueError("regional state requires sample-aligned gNB observations")
        # Each slot contains local values, an explicit presence bit and no fake
        # padded gNB state. The gNB IDs are retained in gnb_ids/action mapping.
        width = OBSERVATION_DIM + 1
        slots: list[float] = []
        for i in range(max_gnbs):
            if i < len(ordered):
                slots.extend(ordered[i].values)
                slots.append(1.0)
            else:
                slots.extend([0.0] * width)
        first = ordered[0]
        return RegionalObservation(tuple(x.gnb_id for x in ordered), first.sequence,
                                   first.begin_sim_ns, first.end_sim_ns, tuple(slots))


def read_measurement_windows(path: str | Path) -> dict[tuple[int, int], list[dict[str, str]]]:
    """Group complete actual CSV records by (sequence, gNB)."""
    grouped: dict[tuple[int, int], list[dict[str, str]]] = {}
    with Path(path).open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if not row.get("scope") or not row.get("sequence") or not row.get("gnb_id"):
                continue
            key = (int(row["sequence"]), int(row["gnb_id"]))
            grouped.setdefault(key, []).append(row)
    return grouped
