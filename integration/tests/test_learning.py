import tempfile
import unittest
import json
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import torch

from integration.learning.hero_ppo import (
    ActionRecord, ExperimentalRewardConfig, PpoConfig, SliceRatioPPO, Transition,
    UeAwareRewardConfig, calculate_experimental_reward,
    calculate_ue_aware_reward, normalized_jain_fairness, validate_ratio_groups,
)
from integration.learning.state import (HeroStateBuilder, LocalObservation, OBSERVATION_DIM,
    UE_AWARE_FEATURES, UE_AWARE_OBSERVATION_DIM, RegionalObservation)
from integration.learning.tiers import LocalPpoAgent, RegionalPpoAgent
from integration.learning.runtime_controller import RuntimeController, _outcome_terms


def transition_batch(agent, count=8):
    transitions = []
    for i in range(count):
        obs = np.full(agent.observation_dim, i / count, dtype=np.float32)
        action = agent.act(obs)
        next_obs = np.full(agent.observation_dim, (i + 1) / count, dtype=np.float32)
        transitions.append(Transition(tuple(obs), action.ratios, action.log_probability,
                                       float((i % 3) - 1), tuple(next_obs), i == count - 1,
                                       policy_version=action.policy_version))
    return transitions


class LearningTests(unittest.TestCase):
    def test_dirichlet_actions_and_validation(self):
        policy = SliceRatioPPO(4, 2, PpoConfig(seed=9, hidden_sizes=(16,), minibatch_size=4))
        sampled = policy.act([0.1, 0.2, 0.3, 0.4])
        self.assertEqual(len(sampled.ratios), 2)
        np.testing.assert_allclose(np.asarray(sampled.ratios).sum(axis=1), [1.0, 1.0], atol=1e-5)
        self.assertTrue(np.isfinite(sampled.log_probability))
        deterministic = policy.act([0.1, 0.2, 0.3, 0.4], deterministic=True)
        self.assertEqual(deterministic.ratios, policy.act([0.1, 0.2, 0.3, 0.4], True).ratios)
        with self.assertRaises(ValueError):
            validate_ratio_groups([[0.2, 0.2, 0.2]], 1)
        with torch.no_grad():
            distribution, _ = policy.model(policy._observation([0.1, 0.2, 0.3, 0.4]))
            sampled_tensor = torch.as_tensor(sampled.ratios, dtype=torch.float32).unsqueeze(0)
            evaluated = float(distribution.log_prob(sampled_tensor).sum().item())
        self.assertAlmostEqual(sampled.log_probability, evaluated, places=5)

    def test_ratio_normalization_matches_strict_native_simplex_and_reward_uses_real_units(self):
        ratios = validate_ratio_groups([[0.1, 0.2, 0.70000004]], 1)[0]
        self.assertAlmostEqual(float(ratios.sum()), 1.0, places=14)
        cell = {"scope": "cell", "dl_delivered_sdu_throughput_mbps": "50",
                "ul_delivered_sdu_throughput_mbps": "50",
                "dl_pdcp_delay_mean_ns": "500000000", "ul_pdcp_delay_mean_ns": "500000000"}
        ue = {"scope": "ue", "required_throughput_bps": "5000000",
              "dl_delivered_sdu_throughput_mbps": "5"}
        terms = _outcome_terms([[cell, ue]], 0.0)
        self.assertAlmostEqual(terms["throughput"], 0.5)
        self.assertAlmostEqual(terms["latency"], 0.5)
        self.assertAlmostEqual(terms["sla"], 1.0)
        self.assertEqual(terms["reliability"], 0.0)

    def test_policy_optimization_and_checkpoint(self):
        config = PpoConfig(seed=12, hidden_sizes=(16,), minibatch_size=4, update_epochs=2)
        agent = SliceRatioPPO(5, 1, config)
        before = [p.detach().clone() for p in agent.model.parameters()]
        result = agent.update(transition_batch(agent))
        self.assertEqual(agent.policy_version, 1)
        self.assertTrue(all(np.isfinite(v) for v in result.values()))
        self.assertTrue(any(not torch.equal(a, b) for a, b in zip(before, agent.model.parameters())))
        with tempfile.TemporaryDirectory() as td:
            checkpoint = Path(td) / "policy.pt"
            agent.save(checkpoint, {"tier": "local"})
            restored = SliceRatioPPO(5, 1, config)
            metadata = restored.load(checkpoint)
            self.assertEqual(metadata["tier"], "local")
            self.assertEqual(agent.act([0] * 5, True).ratios,
                             restored.act([0] * 5, True).ratios)

    def test_separate_local_and_regional_agents(self):
        local = LocalPpoAgent(PpoConfig(hidden_sizes=(8,)))
        regional = RegionalPpoAgent(3, PpoConfig(hidden_sizes=(8,)))
        self.assertEqual(local.policy.observation_dim, OBSERVATION_DIM)
        self.assertEqual(regional.policy.action_groups, 3)
        self.assertNotEqual(local.policy.model.actor.weight.data_ptr(),
                            regional.policy.model.actor.weight.data_ptr())

    def test_state_provenance_missingness_and_regional_identity(self):
        builder = HeroStateBuilder()
        rows = []
        for gnb in (102, 101):
            cell = {"scope": "cell", "gnb_id": str(gnb), "sequence": "7",
                    "begin_sim_ns": "600", "end_sim_ns": "700", "active_ues": "2",
                    "connected_ues": "3", "available_prbs": "106",
                    "dl_sinr_linear_sum": "20", "dl_sinr_samples": "4",
                    "ul_sinr_linear_sum": "8", "ul_sinr_samples": "2",
                    "dl_cqi_sum": "36", "dl_cqi_samples": "6", "dl_mcs_sum": "80",
                    "dl_mcs_samples": "4", "ul_mcs_sum": "60", "ul_mcs_samples": "3",
                    "dl_delivered_sdu_throughput_mbps": "4.0",
                    "ul_delivered_sdu_throughput_mbps": "2.0",
                    "dl_pdcp_delay_mean_ns": "1000000", "ul_pdcp_delay_mean_ns": "2000000",
                    "dl_allocated_prb_grant_sum": "500", "ul_allocated_prb_grant_sum": "300",
                    "dl_allocated_prb_symbols": "2000", "ul_allocated_prb_symbols": "1000"}
            ue = {"scope": "ue", "gnb_id": str(gnb), "sequence": "7", "slice_id": "1",
                  "required_throughput_bps": "5000000", "dl_delivered_sdu_throughput_mbps": "4.0"}
            rows.extend((cell, ue))
        local_a = builder.build_local(rows[:2], (0.4, 0.3, 0.3))
        local_b = builder.build_local(rows[2:], (0.4, 0.3, 0.3))
        self.assertEqual(len(local_a.values), OBSERVATION_DIM)
        # BLER and queue data are unavailable, represented as (0, mask=0).
        feature_index = 5
        self.assertEqual(local_a.values[2 * feature_index:2 * feature_index + 2], (0.0, 0.0))
        regional = builder.build_regional([local_a, local_b], max_gnbs=3)
        self.assertEqual(regional.gnb_ids, (101, 102))
        self.assertEqual(len(regional.values), 3 * (OBSERVATION_DIM + 1))
        controller = RegionalPpoAgent(3, PpoConfig(hidden_sizes=(8,)))
        actions = controller.act(regional, deterministic=True)
        self.assertEqual(list(actions.ratios_by_gnb), [101, 102])
        np.testing.assert_allclose(np.asarray(list(actions.ratios_by_gnb.values())).sum(axis=1), [1, 1], atol=1e-5)
        self.assertTrue(np.isfinite(actions.sampled.log_probability))
        transition = Transition(regional.values, actions.sampled.ratios,
                                actions.sampled.log_probability, 0.25,
                                regional.values, True,
                                policy_version=actions.sampled.policy_version,
                                node_ids=regional.gnb_ids)
        metrics = controller.policy.update([transition])
        self.assertTrue(all(np.isfinite(v) for v in metrics.values()))

    def test_experimental_reward_requires_normalized_terms(self):
        config = ExperimentalRewardConfig(1.0, 0.5, 0.25, 2.0, 0.1)
        reward = calculate_experimental_reward({"throughput": 0.8, "latency": 0.6,
            "reliability": 1.0, "sla": 0.5, "control_cost": 0.2}, config)
        self.assertAlmostEqual(reward, 2.33)
        with self.assertRaises(ValueError):
            calculate_experimental_reward({"throughput": 1.0}, config)

    def test_ue_aware_state_estimates_real_demand_with_fixed_dimension(self):
        cell = {"scope": "cell", "gnb_id": "101", "sequence": "1",
            "begin_sim_ns": "0", "end_sim_ns": "1000000000",
            "applied_embb_ratio": "0.5", "applied_urllc_ratio": "0.3", "applied_mmtc_ratio": "0.2",
            "dl_slice_budget_embb": "60", "dl_slice_budget_urllc": "30", "dl_slice_budget_mmtc": "10",
            "dl_slice_used_embb": "40", "dl_slice_used_urllc": "20", "dl_slice_used_mmtc": "5"}
        ue = {"scope": "ue", "gnb_id": "101", "sequence": "1", "connected_ues": "1",
            "slice_id": "1", "offered_rate_bps": "1000", "required_throughput_bps": "1000",
            "dl_rx_pdcp_sdu_bytes": "1000", "dl_delivered_sdu_throughput_mbps": "0.008",
            "ul_delivered_sdu_throughput_mbps": "0", "dl_allocated_prb_symbols": "100",
            "prbs_per_rbg": "2", "dl_pdcp_delay_mean_ns": "20000000", "delay_target_ms": "10",
            "dl_sinr_linear_sum": "100", "dl_sinr_samples": "1",
            "dl_cqi_sum": "10", "dl_cqi_samples": "1", "dl_mcs_sum": "20", "dl_mcs_samples": "1"}
        observation = HeroStateBuilder().build_local_ue_aware([cell, ue], (0.5, 0.3, 0.2))
        self.assertEqual(len(observation.values), UE_AWARE_OBSERVATION_DIM)
        self.assertTrue(np.isfinite(observation.values).all())
        embb = observation.diagnostics["slices"]["embb"]
        self.assertTrue(embb["demand_complete"])
        self.assertAlmostEqual(embb["estimated_demand_units"], 6.25)
        self.assertEqual(embb["ue_satisfaction"], [0.7])
        self.assertIsNone(observation.diagnostics["slices"]["urllc"]["reliability_violation"])
        loss_index = UE_AWARE_FEATURES.index("packet_loss_violation")
        self.assertEqual(observation.values[2 * loss_index:2 * loss_index + 2], (0.0, 0.0))
        doubled = dict(ue, required_throughput_bps="2000")
        higher_demand = HeroStateBuilder().build_local_ue_aware([cell, doubled], (0.5, 0.3, 0.2))
        self.assertAlmostEqual(higher_demand.diagnostics["slices"]["embb"]["estimated_demand_units"], 2 * embb["estimated_demand_units"])

    def test_normalized_jain_fairness_edge_cases(self):
        self.assertEqual(normalized_jain_fairness([]), 0.0)
        self.assertEqual(normalized_jain_fairness([0.0]), 1.0)
        self.assertEqual(normalized_jain_fairness([0.0, 0.0]), 0.0)
        self.assertEqual(normalized_jain_fairness([0.4, 0.4, 0.4]), 1.0)
        value = normalized_jain_fairness([0.1, 0.9])
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 1.0)
        with self.assertRaises(ValueError):
            normalized_jain_fairness([0.5, float("nan")])

    def test_local_episode_rewards_use_real_boundaries_and_max_episode_limit(self):
        class FakeTrajectory:
            def __init__(self): self.items = []
            def append(self, item): self.items.append(item)
            def __len__(self): return len(self.items)
            def snapshot(self): return tuple(self.items)
            def clear(self): self.items.clear()

        class FakePolicy:
            policy_version = 0
            def update(self, transitions): return {"actor_loss": 0.0, "critic_loss": 0.0}

        class FakeAgent:
            def __init__(self): self.trajectory, self.policy = FakeTrajectory(), FakePolicy()
            def checkpoint(self, path): pass

        with tempfile.TemporaryDirectory() as td:
            controller = RuntimeController.__new__(RuntimeController)
            controller.args = SimpleNamespace(mode="train", max_episodes=2,
                episode_transitions=2, update_every=3, checkpoint_dir=td)
            controller.formulation = "ue-aware"
            controller.ue_reward_config = UeAwareRewardConfig()
            controller.pending_local = {}
            controller.episode_state = {}
            controller.local_updates = {}
            controller._logged_local_schemas = set()
            controller.experience_file = Path(td) / "episodes.jsonl"
            controller.transitions = 0
            agent = FakeAgent()
            controller._local_agent = lambda node_id: agent
            applied = [1 / 3, 1 / 3, 1 / 3]
            slices = {name: {"active_ue_count": 1, "ue_satisfaction": [1.0],
                "estimated_demand_units": 1.0, "actual_budget_units": 1.0,
                "actual_mac_used_units": 1.0, "ue_demand_units": [1.0],
                "qos_satisfaction": 1.0, "worst_ue_satisfaction": 1.0,
                "satisfied_ue_fraction": 1.0} for name in ("embb", "urllc", "mmtc")}
            rows = [{"scope": "ue", "slice_id": str(index), "connected_ues": "1",
                "prbs_per_rbg": "1", "dl_allocated_prb_symbols": "1",
                "dl_mac_scheduled_tb_bytes": "1", "dl_rx_pdcp_sdu_bytes": "1"}
                for index in (1, 2, 3)]
            for step in range(1, 6):
                obs = LocalObservation(101, step, step - 1, step,
                    tuple([0.0] * UE_AWARE_OBSERVATION_DIM),
                    {"applied_ratios": applied, "capacity_units": 3.0, "slices": slices})
                old = LocalObservation(101, step - 1, step - 2, step - 1,
                    tuple([0.0] * UE_AWARE_OBSERVATION_DIM))
                controller.pending_local[101] = (old,
                    ActionRecord((tuple(applied),), 0.0, 0.0, 0), 0.0, tuple(applied))
                controller._record_local_transition(101, obs, rows)
            records = [json.loads(line) for line in controller.experience_file.read_text().splitlines()]
            episodes = [record for record in records if record["record_type"] == "episode"]
            transitions = [record for record in records if record["record_type"] == "transition"]
            self.assertEqual([record["episode_id"] for record in episodes], [1, 2])
            self.assertEqual([record["episode_steps"] for record in episodes], [2, 2])
            self.assertTrue(all(abs(record["mean_reward"] - 0.95) < 1e-8 for record in episodes))
            self.assertTrue(all(record["reward_min"] == -0.05 and record["reward_max"] == 0.95 for record in episodes))
            self.assertTrue(all(record["simulator_reset"] is False for record in episodes))
            self.assertEqual(len(transitions), 4, "training stops after the configured maximum episodes")
            self.assertTrue(transitions[1]["truncated"])
            self.assertTrue(transitions[3]["truncated"])

    def test_bounded_reward_global_and_state_action_bounds(self):
        config = UeAwareRewardConfig()
        report = calculate_ue_aware_reward({"Q": 0.7, "A": 0.8, "E": 0.6, "F": 0.5, "H": 0.4}, config)
        self.assertAlmostEqual(report["reward"], 0.63)
        self.assertAlmostEqual(report["global_lower_bound"], -0.05)
        self.assertAlmostEqual(report["global_upper_bound"], 0.95)
        self.assertLessEqual(report["state_action_lower_bound"], report["reward"])
        self.assertGreaterEqual(report["state_action_upper_bound"], report["reward"])
        for component in ("Q", "A", "E", "F", "H"):
            with self.assertRaises(ValueError):
                calculate_ue_aware_reward({"Q": 0.5, "A": 0.5, "E": 0.5, "F": 0.5, "H": 0.5,
                                            component: 1.1})
        with self.assertRaises(ValueError):
            UeAwareRewardConfig(.5, .2, .1, .1, .05).validate()

    def test_ue_aware_local_agent_uses_distinct_checkpoint_schema(self):
        local = LocalPpoAgent(PpoConfig(hidden_sizes=(8,)), ue_aware=True)
        self.assertEqual(local.policy.observation_dim, UE_AWARE_OBSERVATION_DIM)
        observation = HeroStateBuilder().build_local_ue_aware([
            {"scope":"cell", "gnb_id":"101", "sequence":"1", "begin_sim_ns":"0", "end_sim_ns":"1"}
        ])
        action = local.act(observation)
        self.assertEqual(len(action.ratios), 1)

    def test_rollout_version_guard_and_real_transition_evaluation(self):
        agent = SliceRatioPPO(2, config=PpoConfig(hidden_sizes=(8,)))
        transitions = transition_batch(agent, count=3)
        report = SliceRatioPPO.evaluate([transitions[:2], transitions[2:]])
        self.assertEqual(report["episodes"], 2.0)
        self.assertTrue(np.isfinite(report["mean_return"]))


if __name__ == "__main__":
    unittest.main()
