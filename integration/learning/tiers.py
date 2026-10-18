"""Tier-specific PPO agents; each instance owns its own model and rollout."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .hero_ppo import ActionRecord, PpoConfig, RolloutBuffer, SliceRatioPPO
from .state import (OBSERVATION_DIM, UE_AWARE_OBSERVATION_DIM, LocalObservation,
                    RegionalObservation)


class LocalPpoAgent:
    def __init__(self, config: PpoConfig | None = None, device: str = "cpu",
                 ue_aware: bool = False):
        self.ue_aware = ue_aware
        observation_dim = UE_AWARE_OBSERVATION_DIM if ue_aware else OBSERVATION_DIM
        self.policy = SliceRatioPPO(observation_dim, 1, config, device)
        self.trajectory = RolloutBuffer()

    def act(self, observation: LocalObservation, deterministic: bool = False) -> ActionRecord:
        return self.policy.act(observation.values, deterministic)

    def checkpoint(self, path: str | Path) -> None:
        schema = "hero-local-ue-aware-v1" if self.ue_aware else "hero-local-v1"
        self.policy.save(path, {"tier": "local", "observation_schema": schema})


@dataclass(frozen=True)
class RegionalActionRecord:
    gnb_ids: tuple[int, ...]
    ratios_by_gnb: dict[int, tuple[float, float, float]]
    sampled: ActionRecord  # retains PPO log probability/value and padded groups


class RegionalPpoAgent:
    def __init__(self, max_gnbs: int, config: PpoConfig | None = None, device: str = "cpu"):
        if max_gnbs < 1:
            raise ValueError("max_gnbs must be positive")
        self.max_gnbs = max_gnbs
        self.policy = SliceRatioPPO((OBSERVATION_DIM + 1) * max_gnbs,
                                    max_gnbs, config, device)
        self.trajectory = RolloutBuffer()

    def act(self, observation: RegionalObservation,
            deterministic: bool = False) -> RegionalActionRecord:
        if len(observation.gnb_ids) > self.max_gnbs:
            raise ValueError("regional observation exceeds configured gNB capacity")
        sampled = self.policy.act(observation.values, deterministic,
                                  active_groups=len(observation.gnb_ids))
        # Position is deterministic sorted gNB identity order. Inactive padded
        # policy groups are neither sampled nor included in PPO log probability.
        by_gnb = {gnb_id: sampled.ratios[index]
                  for index, gnb_id in enumerate(observation.gnb_ids)}
        return RegionalActionRecord(observation.gnb_ids, by_gnb, sampled)

    def checkpoint(self, path: str | Path) -> None:
        self.policy.save(path, {"tier": "regional", "max_gnbs": self.max_gnbs,
                                "observation_schema": "hero-regional-v1"})
