"""Reusable local/regional PPO primitives for HERO slice-ratio policies.

The learner is deliberately decoupled from ns-3 and FlexRIC.  Callers must
provide aligned transitions derived from a real environment before training.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
import json
import math

import numpy as np
import torch
from torch import nn
from torch.distributions import Dirichlet


SLICE_NAMES = ("eMBB", "URLLC", "mMTC")


@dataclass(frozen=True)
class PpoConfig:
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_epsilon: float = 0.2
    learning_rate: float = 3e-4
    value_coefficient: float = 0.5
    entropy_coefficient: float = 0.01
    update_epochs: int = 4
    minibatch_size: int = 64
    hidden_sizes: tuple[int, ...] = (128, 128)
    concentration_floor: float = 1e-4
    gradient_clip_norm: float = 0.5
    target_kl: float | None = None
    seed: int = 1

    def validate(self) -> None:
        if not 0 < self.gamma <= 1 or not 0 <= self.gae_lambda <= 1:
            raise ValueError("gamma must be in (0,1] and gae_lambda in [0,1]")
        if not 0 < self.clip_epsilon < 1 or self.learning_rate <= 0:
            raise ValueError("invalid PPO clipping or learning rate")
        if self.update_epochs < 1 or self.minibatch_size < 1 or not self.hidden_sizes:
            raise ValueError("PPO epochs, minibatch and hidden sizes must be positive")
        if self.concentration_floor <= 0:
            raise ValueError("concentration_floor must be positive")
        if not math.isfinite(self.gradient_clip_norm) or self.gradient_clip_norm <= 0:
            raise ValueError("gradient_clip_norm must be positive and finite")
        if self.target_kl is not None and (not math.isfinite(self.target_kl) or self.target_kl <= 0):
            raise ValueError("target_kl must be positive and finite when configured")


@dataclass(frozen=True)
class ActionRecord:
    ratios: tuple[tuple[float, float, float], ...]
    log_probability: float
    value: float
    policy_version: int


@dataclass(frozen=True)
class Transition:
    observation: tuple[float, ...]
    action: tuple[tuple[float, float, float], ...]
    old_log_probability: float
    reward: float
    next_observation: tuple[float, ...]
    terminated: bool
    truncated: bool = False
    simulation_time_ns: int = 0
    policy_version: int = 0
    node_ids: tuple[int, ...] = ()


class EnvironmentStepResult(Protocol):
    """Contract only; a deterministic ns-3/Slice Manager adapter is absent."""
    observation: Sequence[float]
    reward: float
    terminated: bool
    truncated: bool
    simulation_time_ns: int


class HeroEnvironment(Protocol):
    def reset(self, *, seed: int | None = None) -> Sequence[float]: ...
    def step(self, action: Sequence[Sequence[float]]) -> EnvironmentStepResult: ...


class RolloutBuffer:
    """Owns one tier's transitions and prevents mixed-policy rollouts."""
    def __init__(self):
        self._items: list[Transition] = []

    def append(self, transition: Transition) -> None:
        if self._items and transition.policy_version != self._items[0].policy_version:
            raise ValueError("cannot mix policy versions in one PPO rollout")
        self._items.append(transition)

    def snapshot(self) -> tuple[Transition, ...]:
        return tuple(self._items)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


class ActorCritic(nn.Module):
    """A Dirichlet actor with one independent three-ratio simplex per node."""

    def __init__(self, observation_dim: int, action_groups: int,
                 hidden_sizes: Sequence[int], concentration_floor: float):
        super().__init__()
        if observation_dim < 1 or action_groups < 1:
            raise ValueError("observation_dim and action_groups must be positive")
        layers: list[nn.Module] = []
        width = observation_dim
        for hidden in hidden_sizes:
            if hidden < 1:
                raise ValueError("hidden layer sizes must be positive")
            layers.extend((nn.Linear(width, hidden), nn.Tanh()))
            width = hidden
        self.shared = nn.Sequential(*layers)
        self.actor = nn.Linear(width, action_groups * len(SLICE_NAMES))
        self.critic = nn.Linear(width, 1)
        self.action_groups = action_groups
        self.concentration_floor = concentration_floor

    def forward(self, observations: torch.Tensor) -> tuple[Dirichlet, torch.Tensor]:
        hidden = self.shared(observations)
        concentrations = torch.nn.functional.softplus(self.actor(hidden))
        concentrations = concentrations.reshape(-1, self.action_groups, len(SLICE_NAMES))
        concentrations = concentrations + self.concentration_floor
        return Dirichlet(concentrations), self.critic(hidden).squeeze(-1)


class SliceRatioPPO:
    """PPO for a local (one group) or regional (one group per gNB) policy."""

    def __init__(self, observation_dim: int, action_groups: int = 1,
                 config: PpoConfig | None = None, device: str = "cpu"):
        self.config = config or PpoConfig()
        self.config.validate()
        torch.manual_seed(self.config.seed)
        np.random.seed(self.config.seed)
        self.device = torch.device(device)
        self.model = ActorCritic(observation_dim, action_groups,
                                 self.config.hidden_sizes,
                                 self.config.concentration_floor).to(self.device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.config.learning_rate)
        self.observation_dim = observation_dim
        self.action_groups = action_groups
        self.policy_version = 0

    def _observation(self, value: Sequence[float]) -> torch.Tensor:
        array = np.asarray(value, dtype=np.float32)
        if array.shape != (self.observation_dim,) or not np.isfinite(array).all():
            raise ValueError(f"expected {self.observation_dim} finite observation values")
        return torch.as_tensor(array, dtype=torch.float32, device=self.device).unsqueeze(0)

    def act(self, observation: Sequence[float], deterministic: bool = False,
            active_groups: int | None = None) -> ActionRecord:
        active_groups = self.action_groups if active_groups is None else active_groups
        if not 1 <= active_groups <= self.action_groups:
            raise ValueError("active_groups must be in [1, action_groups]")
        with torch.no_grad():
            distribution, value = self.model(self._observation(observation))
            active_distribution = Dirichlet(distribution.concentration[:, :active_groups, :])
            # Deterministic inference uses the Dirichlet expectation. Training samples.
            action = active_distribution.mean if deterministic else active_distribution.sample()
            action = validate_ratio_groups(action.squeeze(0).cpu().numpy(), active_groups)
            log_probability = active_distribution.log_prob(torch.as_tensor(action, dtype=torch.float32,
                                                                            device=self.device).unsqueeze(0)).sum()
        return ActionRecord(tuple(tuple(float(v) for v in row) for row in action),
                            float(log_probability.cpu()), float(value.item()), self.policy_version)

    def evaluate_actions(self, observations: torch.Tensor, actions: torch.Tensor,
                         group_mask: torch.Tensor | None = None) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        distribution, values = self.model(observations)
        log_probabilities = distribution.log_prob(actions)
        entropies = distribution.entropy()
        if group_mask is not None:
            log_probabilities = log_probabilities * group_mask
            entropies = entropies * group_mask
        log_probabilities = log_probabilities.sum(dim=-1)
        entropies = entropies.sum(dim=-1)
        return log_probabilities, entropies, values

    def update(self, transitions: Sequence[Transition]) -> dict[str, float]:
        if not transitions:
            raise ValueError("PPO update requires at least one transition")
        versions = {t.policy_version for t in transitions}
        if versions != {self.policy_version}:
            raise ValueError("PPO rollout policy_version must match the current policy")
        for transition in transitions:
            active = len(transition.action)
            if self.action_groups > 1:
                if len(transition.node_ids) != active or len(set(transition.node_ids)) != active:
                    raise ValueError("regional transition IDs must match active action groups in order")
            elif transition.node_ids and len(transition.node_ids) != 1:
                raise ValueError("local transition may identify at most one gNB")
        obs = np.asarray([t.observation for t in transitions], dtype=np.float32)
        next_obs = np.asarray([t.next_observation for t in transitions], dtype=np.float32)
        if any(not 1 <= len(t.action) <= self.action_groups for t in transitions):
            raise ValueError("each transition must contain 1..action_groups active ratio vectors")
        actions = np.tile(np.asarray([1/3, 1/3, 1/3], dtype=np.float32),
                          (len(transitions), self.action_groups, 1))
        group_mask_np = np.zeros((len(transitions), self.action_groups), dtype=np.float32)
        for i, transition in enumerate(transitions):
            active = np.asarray(transition.action, dtype=np.float32)
            validate_ratio_groups(active, len(active))
            actions[i, :len(active)] = active
            group_mask_np[i, :len(active)] = 1.0
        if obs.shape != (len(transitions), self.observation_dim):
            raise ValueError("transition observation dimensions do not match agent")
        if next_obs.shape != obs.shape or actions.shape != (len(transitions), self.action_groups, 3):
            raise ValueError("transition next-state/action dimensions do not match agent")
        rewards = np.asarray([t.reward for t in transitions], dtype=np.float32)
        if not np.isfinite(rewards).all():
            raise ValueError("transition rewards must be finite")
        old_logp = np.asarray([t.old_log_probability for t in transitions], dtype=np.float32)
        if not np.isfinite(old_logp).all():
            raise ValueError("old log probabilities must be finite")
        obs_t = torch.as_tensor(obs, device=self.device)
        next_t = torch.as_tensor(next_obs, device=self.device)
        actions_t = torch.as_tensor(actions, device=self.device)
        group_mask_t = torch.as_tensor(group_mask_np, device=self.device)
        rewards_t = torch.as_tensor(rewards, device=self.device)
        old_logp_t = torch.as_tensor(old_logp, device=self.device)
        with torch.no_grad():
            _, values = self.model(obs_t)
            _, next_values = self.model(next_t)
            advantages = torch.zeros_like(rewards_t)
            gae = torch.zeros((), device=self.device)
            for i in reversed(range(len(transitions))):
                # Truncation ends the rollout but bootstraps; termination does not.
                bootstrap = 0.0 if transitions[i].terminated else float(next_values[i].item())
                delta = rewards_t[i] + self.config.gamma * bootstrap - values[i]
                continuation = 0.0 if (transitions[i].terminated or transitions[i].truncated) else 1.0
                gae = delta + self.config.gamma * self.config.gae_lambda * continuation * gae
                advantages[i] = gae
            returns = advantages + values
            if len(transitions) > 1:
                advantages = (advantages - advantages.mean()) / (advantages.std(unbiased=False) + 1e-8)

        indices = np.arange(len(transitions))
        metrics: dict[str, list[float]] = {k: [] for k in
            ("actor_loss", "critic_loss", "entropy", "approx_kl", "clip_fraction", "gradient_norm")}
        for _ in range(self.config.update_epochs):
            np.random.shuffle(indices)
            for start in range(0, len(indices), self.config.minibatch_size):
                batch = torch.as_tensor(indices[start:start + self.config.minibatch_size],
                                        dtype=torch.long, device=self.device)
                logp, entropy, new_values = self.evaluate_actions(obs_t[batch], actions_t[batch],
                                                                  group_mask_t[batch])
                log_ratio = logp - old_logp_t[batch]
                ratio = torch.exp(log_ratio)
                unclipped = ratio * advantages[batch]
                clipped = torch.clamp(ratio, 1-self.config.clip_epsilon,
                                       1+self.config.clip_epsilon) * advantages[batch]
                actor_loss = -torch.minimum(unclipped, clipped).mean()
                critic_loss = torch.nn.functional.mse_loss(new_values, returns[batch])
                entropy_mean = entropy.mean()
                loss = (actor_loss + self.config.value_coefficient * critic_loss
                        - self.config.entropy_coefficient * entropy_mean)
                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                grad_norm = nn.utils.clip_grad_norm_(self.model.parameters(), self.config.gradient_clip_norm)
                self.optimizer.step()
                with torch.no_grad():
                    approx_kl = ((ratio - 1) - log_ratio).mean()
                    clip_fraction = ((ratio - 1).abs() > self.config.clip_epsilon).float().mean()
                for key, value in (("actor_loss", actor_loss), ("critic_loss", critic_loss),
                                   ("entropy", entropy_mean), ("approx_kl", approx_kl),
                                   ("clip_fraction", clip_fraction), ("gradient_norm", grad_norm)):
                    metrics[key].append(float(value.item()))
                if self.config.target_kl is not None and float(approx_kl.item()) > self.config.target_kl:
                    break
            if self.config.target_kl is not None and metrics["approx_kl"] and metrics["approx_kl"][-1] > self.config.target_kl:
                break
        self.policy_version += 1
        return {key: float(np.mean(values)) for key, values in metrics.items()}

    def save(self, path: str | Path, metadata: Mapping[str, Any] | None = None) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        torch.save({"model": self.model.state_dict(), "optimizer": self.optimizer.state_dict(),
                    "config": asdict(self.config), "observation_dim": self.observation_dim,
                    "action_groups": self.action_groups, "policy_version": self.policy_version,
                    "metadata": dict(metadata or {})}, target)

    def load(self, path: str | Path) -> Mapping[str, Any]:
        checkpoint = torch.load(path, map_location=self.device, weights_only=False)
        if checkpoint["observation_dim"] != self.observation_dim or checkpoint["action_groups"] != self.action_groups:
            raise ValueError("checkpoint dimensions do not match agent")
        saved_config = checkpoint.get("config", {})
        saved_hidden = tuple(saved_config.get("hidden_sizes", ()))
        if saved_hidden != tuple(self.config.hidden_sizes) or float(saved_config.get("concentration_floor", -1)) != self.config.concentration_floor:
            raise ValueError("checkpoint actor architecture does not match agent")
        self.model.load_state_dict(checkpoint["model"])
        self.optimizer.load_state_dict(checkpoint["optimizer"])
        self.policy_version = int(checkpoint["policy_version"])
        return checkpoint.get("metadata", {})

    @staticmethod
    def evaluate(episodes: Sequence[Sequence[Transition]]) -> dict[str, float]:
        """Summarize returns only from caller-supplied real reward transitions."""
        if not episodes or any(not episode for episode in episodes):
            raise ValueError("evaluation requires non-empty real transition episodes")
        returns = [sum(float(t.reward) for t in episode) for episode in episodes]
        if not np.isfinite(returns).all():
            raise ValueError("evaluation rewards must be finite")
        return {"episodes": float(len(returns)), "mean_return": float(np.mean(returns)),
                "std_return": float(np.std(returns)),
                "mean_episode_length": float(np.mean([len(e) for e in episodes]))}


def validate_ratio_groups(values: np.ndarray | Sequence[Sequence[float]], groups: int,
                          tolerance: float = 1e-5) -> np.ndarray:
    ratios = np.asarray(values, dtype=np.float64)
    if ratios.shape != (groups, len(SLICE_NAMES)) or not np.isfinite(ratios).all():
        raise ValueError(f"expected finite ratios with shape ({groups}, 3)")
    if np.any(ratios < 0) or np.any(ratios > 1) or not np.allclose(ratios.sum(axis=1), 1.0, atol=tolerance):
        raise ValueError("each slice-ratio vector must be nonnegative and sum to one")
    # Torch's float32 Dirichlet samples can sum to 1 within tolerance while
    # failing the stricter native SliceBudgetManager invariant. Normalize in
    # float64 before logging, replay, and simulator dispatch.
    return ratios / ratios.sum(axis=1, keepdims=True)


@dataclass(frozen=True)
class ExperimentalRewardConfig:
    """Weights are experiment settings, not finalized HERO paper constants."""
    throughput: float
    latency: float
    reliability: float
    sla: float
    control_cost: float
    experimental: bool = True

    def validate(self) -> None:
        if not self.experimental:
            raise ValueError("reward must be explicitly marked experimental pending paper verification")
        if not all(math.isfinite(v) for v in (self.throughput, self.latency, self.reliability,
                                               self.sla, self.control_cost)):
            raise ValueError("reward coefficients must be finite")


def calculate_experimental_reward(terms: Mapping[str, float],
                                  config: ExperimentalRewardConfig) -> float:
    """Apply configured weights to caller-supplied normalized real outcome terms."""
    config.validate()
    required = ("throughput", "latency", "reliability", "sla", "control_cost")
    if set(terms) != set(required) or not all(math.isfinite(float(terms[k])) for k in required):
        raise ValueError("provide exactly five finite, externally normalized outcome terms")
    result = (config.throughput * terms["throughput"] + config.latency * terms["latency"]
              + config.reliability * terms["reliability"] + config.sla * terms["sla"]
              - config.control_cost * terms["control_cost"])
    if not math.isfinite(result):
        raise ValueError("reward is not finite")
    return result


@dataclass(frozen=True)
class UeAwareRewardConfig:
    """Initial experimental weights from the implementation task."""
    quality: float = 0.50
    adequacy: float = 0.20
    useful_efficiency: float = 0.15
    fairness: float = 0.10
    action_stability: float = 0.05

    def validate(self) -> None:
        weights = (self.quality, self.adequacy, self.useful_efficiency,
                   self.fairness, self.action_stability)
        if not all(math.isfinite(value) and value >= 0 for value in weights):
            raise ValueError("UE-aware reward weights must be finite and nonnegative")
        if not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("UE-aware reward weights must sum to one")


def calculate_ue_aware_reward(terms: Mapping[str, float],
                              config: UeAwareRewardConfig | None = None) -> dict[str, float]:
    """Calculate the bounded Q/A/E/F/H reward and verify its analytic bounds."""
    config = config or UeAwareRewardConfig()
    config.validate()
    required = ("Q", "A", "E", "F", "H")
    if set(terms) != set(required):
        raise ValueError("UE-aware reward requires exactly Q, A, E, F and H terms")
    values = {key: float(terms[key]) for key in required}
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values.values()):
        raise ValueError("each UE-aware reward component must be finite and within [0,1]")
    lower = config.adequacy * values["A"] - config.action_stability * values["H"]
    upper = lower + config.quality + config.useful_efficiency + config.fairness
    reward = (config.quality * values["Q"] + config.adequacy * values["A"]
              + config.useful_efficiency * values["E"] + config.fairness * values["F"]
              - config.action_stability * values["H"])
    global_min = -config.action_stability
    global_max = 1.0 - config.action_stability
    tolerance = 1e-12
    if not global_min - tolerance <= reward <= global_max + tolerance:
        raise ArithmeticError("UE-aware reward violates its guaranteed global bounds")
    if not lower - tolerance <= reward <= upper + tolerance:
        raise ArithmeticError("UE-aware reward violates its state-action bounds")
    return {"reward": reward, "global_lower_bound": global_min,
            "global_upper_bound": global_max, "state_action_lower_bound": lower,
            "state_action_upper_bound": upper, **values}


def normalized_jain_fairness(satisfaction: Sequence[float]) -> float:
    """Jain fairness scaled from [1/N, 1] to [0, 1]."""
    values = np.asarray(tuple(satisfaction), dtype=np.float64)
    if values.ndim != 1 or not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1):
        raise ValueError("UE satisfaction values must be a finite vector in [0,1]")
    count = int(values.size)
    if count == 0:
        return 0.0
    if count == 1:
        return 1.0
    square_sum = float(np.dot(values, values))
    jain = (float(values.sum()) ** 2 / (count * square_sum)) if square_sum > 0 else 1.0 / count
    return min(1.0, max(0.0, (jain - 1.0 / count) / (1.0 - 1.0 / count)))


def append_jsonl(path: str | Path, record: Mapping[str, Any]) -> None:
    """Append actual observations/actions/outcomes without fabricating training data."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
