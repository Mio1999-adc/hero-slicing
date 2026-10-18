import tempfile
import unittest
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


class FinishedProcess:
    pid = 90001
    returncode = 0

    @staticmethod
    def poll():
        return 0


class DashboardLaunchTests(unittest.TestCase):
    def test_launch_reuses_existing_ric_listener_without_spawning_second_ric(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            build = Path(temp_dir)
            manager = server.Dashboard()
            spawned = []

            def fake_spawn(name, command, env, suffix):
                spawned.append(name)
                log_path = build / "dashboard-runs" / suffix / f"{name}.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("simulation started\n")
                manager.processes[name] = FinishedProcess()
                manager.log_paths[name] = log_path
                return manager.processes[name]

            config = dict(server.DEFAULT, gnbs=1, ues=1, kpmMonitor=False,
                          enableControl=False, runUntilStopped=False)
            with patch.object(server, "BUILD", build), \
                    patch.object(server, "sctp_listener_inode", return_value="321"), \
                    patch.object(server, "sctp_socket_owner", return_value=(1234, "near-rt-ric")), \
                    patch.object(manager, "spawn", side_effect=fake_spawn):
                result = manager.launch(config)

            self.assertEqual(spawned, ["simulation"])
            self.assertEqual(result["applied"]["ricMode"], "reused-existing-listener")
            self.assertTrue(result["processes"]["ric"]["running"])
            self.assertFalse(result["processes"]["ric"]["managed"])
            self.assertIn("Reusing existing FlexRIC SCTP listener", result["logs"]["ric"])

    def test_profile_validation_keeps_radio_range_and_automatically_selects_radio_only_above_two(self):
        config = dict(server.DEFAULT, gnbs=10, ues=10, connectRic=False,
                      kpmMonitor=False, enableControl=False)
        normalized, errors = server.clean_config(config)
        self.assertEqual(normalized["gnbs"], 10)
        self.assertEqual(errors, [])

        config.update(connectRic=True, kpmMonitor=True, enableControl=True)
        normalized, errors = server.clean_config(config)
        self.assertEqual(errors, [])
        self.assertEqual((normalized["connectRic"], normalized["kpmMonitor"], normalized["enableControl"]),
                         (False, False, False))
        self.assertEqual(normalized["gnbSpacingM"], 500.0)
        self.assertEqual(normalized["ueDistanceM"], 20.0)

    def test_slice_profile_validation_checks_counts_and_probabilities(self):
        config = dict(server.DEFAULT, gnbs=1, ues=9, connectRic=False, kpmMonitor=False,
                      enableControl=False, sliceAssignment="explicit", embbUes=3, urllcUes=3, mmtcUes=3)
        _, errors = server.clean_config(config)
        self.assertEqual(errors, [])
        config["mmtcUes"] = 2
        _, errors = server.clean_config(config)
        self.assertTrue(any("must sum to total UEs" in e for e in errors))
        config.update(sliceAssignment="random", embbProbability=0.4, urllcProbability=0.3, mmtcProbability=0.2)
        _, errors = server.clean_config(config)
        self.assertTrue(any("probabilities must sum to 1" in e for e in errors))

    def test_slice_budget_configuration_is_opt_in_and_validated(self):
        config = dict(server.DEFAULT, connectRic=False, kpmMonitor=False, enableControl=False,
                      sliceAssignment="explicit", enableSliceEnforcement=True,
                      embbRatio=0.5, urllcRatio=0.3, mmtcRatio=0.2)
        normalized, errors = server.clean_config(config)
        self.assertEqual(errors, [])
        self.assertTrue(normalized["enableSliceEnforcement"])
        config["mmtcRatio"] = 0.3
        _, errors = server.clean_config(config)
        self.assertTrue(any("ratios must sum to 1" in e for e in errors))
        config["sliceAssignment"] = "off"
        _, errors = server.clean_config(config)
        self.assertTrue(any("requires explicit or seeded-random" in e for e in errors))

    def test_live_ppo_requires_slice_enforcement_and_validates_tier_and_reward(self):
        config = dict(server.DEFAULT, connectRic=False, kpmMonitor=False, enableControl=False,
                      sliceAssignment="explicit", enableSliceEnforcement=True, enablePpo=True)
        normalized, errors = server.clean_config(config)
        self.assertEqual(errors, [])
        self.assertTrue(normalized["enablePpo"])
        config["enableSliceEnforcement"] = False
        _, errors = server.clean_config(config)
        self.assertTrue(any("requires enabled slice-budget enforcement" in e for e in errors))
        config["enableSliceEnforcement"] = True
        config["ppoTier"] = "unsupported"
        config["ppoRewardLatency"] = -1
        _, errors = server.clean_config(config)
        self.assertTrue(any("ppoTier must be local or regional" in e for e in errors))
        self.assertTrue(any("ppoRewardLatency must be a finite nonnegative" in e for e in errors))

    def test_episode_configuration_requires_positive_limits(self):
        config = dict(server.DEFAULT)
        config["ppoMaxEpisodes"] = 0
        config["ppoEpisodeTransitions"] = 0
        _, errors = server.clean_config(config)
        self.assertTrue(any("ppoMaxEpisodes must be an integer" in e for e in errors))
        self.assertTrue(any("ppoEpisodeTransitions must be an integer" in e for e in errors))

    def test_slice_dashboard_uses_native_csv_and_marks_missing_qos_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            csv_path = run_dir / "measurements.csv"
            csv_path.write_text(
                "sequence,end_sim_ns,gnb_id,scope,imsi,rnti,slice_id,slice_type,application_type,qos_profile,"
                "required_throughput_bps,delay_target_ms,reliability_target,dl_delivered_sdu_throughput_mbps,"
                "dl_allocated_prb_grant_sum,dl_rx_pdcp_sdu_bytes,dl_allocated_prb_symbols\n"
                "1,1000000000,101,ue,1001,1,1,eMBB,video,NGBR_VIDEO_TCP_DEFAULT,10000000,0,0,12,40,1500000,300\n"
                "1,1000000000,101,ue,1002,2,2,URLLC,industrial-control,DGBR_DISCRETE_AUT_SMALL,0,10,0.9999,0.1,5,1280,10\n")
            manager = server.Dashboard()
            manager.applied = {"runDirectory": str(run_dir), "values": {"csv": "measurements.csv"}}
            data = manager.slices()
            self.assertEqual([x["name"] for x in data["counts"]], ["eMBB", "URLLC"])
            self.assertEqual(data["ues"][0]["status"], "SATISFIED")
            self.assertAlmostEqual(data["ues"][0]["throughputSatisfaction"], 1.0)
            self.assertAlmostEqual(data["ues"][0]["resourceEfficiencyBitsPerPrbSymbol"], 40000)
            self.assertEqual(data["ues"][1]["status"], "INSUFFICIENT_DATA")

    def test_learning_dashboard_reads_live_rewards_and_ppo_update_losses_incrementally(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            events = [
                {"record_type": "transition", "tier": "local", "simulation_time_ns": 1_000_000_000,
                 "gnb_ids": [101], "policy_version": 0, "reward": 0.4},
                {"record_type": "transition", "tier": "local", "simulation_time_ns": 1_000_000_000,
                 "gnb_ids": [102], "policy_version": 0, "reward": 0.6},
                {"record_type": "episode_progress", "tier": "local", "simulation_time_ns": 1_500_000_000,
                 "gnb_ids": [102], "episode_id": 1, "episode_steps": 1,
                 "mean_reward": 0.96, "reward_min": -0.05, "reward_max": 0.95,
                 "reward_gap": -0.01, "bound_utilization": 1.01,
                 "total_transitions": 1, "optimizer_updates": 0},
                {"record_type": "update", "tier": "local", "simulation_time_ns": 2_000_000_000,
                 "gnb_ids": [101], "policy_version": 1,
                 "update_count": 1,
                 "metrics": {"actor_loss": -0.1, "critic_loss": 0.25, "clip_fraction": 0.125}},
                {"record_type": "transition", "tier": "regional", "simulation_time_ns": 2_000_000_000,
                 "gnb_ids": [101, 102], "policy_version": 1, "reward": 0.8},
                {"record_type": "episode", "tier": "local", "simulation_time_ns": 2_000_000_000,
                 "gnb_ids": [101], "episode_id": 1, "episode_steps": 2,
                 "mean_reward": 0.7, "best_mean_reward": 0.7, "reward_min": -0.05,
                 "reward_max": 0.95, "bound_utilization": 0.75, "total_transitions": 2,
                 "optimizer_updates": 1, "simulator_reset": False,
                 "episode_boundary": "fixed_control_transition_horizon"},
                {"record_type": "update", "tier": "regional", "simulation_time_ns": 3_000_000_000,
                 "gnb_ids": [101, 102], "policy_version": 2,
                 "metrics": {"actor_loss": 0.05, "critic_loss": 0.12}},
            ]
            (run_dir / "ppo-transitions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in events))
            manager = server.Dashboard()
            manager.applied = {"runDirectory": str(run_dir), "values": {"enablePpo": True,
                              "ppoMode": "train", "ppoTier": "regional"}}

            data = manager.learning()
            self.assertEqual(len(data["rewards"]), 3)
            self.assertEqual({x["series"] for x in data["rewards"]},
                             {"gNB 101 · local PPO", "gNB 102 · local PPO",
                              "Near-RT RIC · regional PPO · 101, 102"})
            self.assertEqual(len(data["errors"]), 5)
            self.assertEqual(len(data["episodes"]), 1)
            self.assertEqual(data["episodes"][0]["episodeId"], 1)
            self.assertAlmostEqual(data["episodes"][0]["rewardGap"], 0.25)
            self.assertFalse(data["episodes"][0]["boundViolation"])
            self.assertEqual(data["currentEpisodes"][0]["gnbIds"], [102])
            self.assertTrue(data["currentEpisodes"][0]["boundViolation"],
                            "dashboard must flag a reward outside the selected episode's bound")
            self.assertIn("critic_loss", {x["metric"] for x in data["errors"]})
            actor = next(x for x in data["errors"] if x["metric"] == "actor_loss")
            self.assertEqual(actor["updateId"], 1)
            self.assertEqual(actor["gnbId"], 101)
            self.assertEqual(actor["trainingRunId"], run_dir.name)
            self.assertIn("clip_fraction", {x["metric"] for x in data["errors"]})
            self.assertEqual(data["episodes"][0]["metric"], "episode_mean_reward")
            self.assertEqual(len(manager.learning()["rewards"]), 3,
                             "subsequent polling must not duplicate already-read JSONL records")

    def test_learning_dashboard_distinguishes_inference_from_training_metrics(self):
        manager = server.Dashboard()
        manager.applied = {"runDirectory": "/unused", "values": {"enablePpo": True,
                          "ppoMode": "infer", "ppoTier": "local"}}
        data = manager.learning()
        self.assertTrue(data["enabled"])
        self.assertEqual(data["mode"], "infer")
        self.assertFalse(data["rewards"])
        self.assertIn("does not calculate training rewards", data["message"])

    def test_large_radio_launch_passes_counts_and_geometry_to_native_scenario(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            build = Path(temp_dir)
            manager = server.Dashboard()
            launched = {}

            def fake_spawn(name, command, env, suffix):
                launched[name] = command
                log_path = build / "dashboard-runs" / suffix / f"{name}.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("simulation started\n")
                manager.processes[name] = FinishedProcess()
                manager.log_paths[name] = log_path
                return manager.processes[name]

            config = dict(server.DEFAULT, gnbs=10, ues=20, gnbSpacingM=750,
                          ueDistanceM=35, connectRic=True, kpmMonitor=True,
                          enableControl=True, runUntilStopped=False, sliceAssignment="random",
                          enableSliceEnforcement=True, enablePpo=True, ppoTier="regional")
            with patch.object(server, "BUILD", build), patch.object(manager, "spawn", side_effect=fake_spawn):
                result = manager.launch(config)

            command = launched["simulation"]
            self.assertIn("--gnbs=10", command)
            self.assertIn("--ues=20", command)
            self.assertIn("--gnbSpacingM=750", command)
            self.assertIn("--ueDistanceM=35", command)
            self.assertIn("--enableE2=0", command)
            self.assertIn("--realtime=0", command)
            self.assertIn("--enablePpo=1", command)
            self.assertIn("--ppoTier=regional", command)
            self.assertIn(f"--ppoCheckpointDir={build / 'ppo-checkpoints' / 'regional-gnbs-10'}", command)
            self.assertFalse(result["applied"]["values"]["connectRic"])
            self.assertTrue(result["requested"]["connectRic"])

    def test_geometry_rejects_ambiguous_nearest_cell_layout(self):
        config = dict(server.DEFAULT, gnbs=2, ues=2, gnbSpacingM=100, ueDistanceM=50)
        _, errors = server.clean_config(config)
        self.assertTrue(any("less than half" in error for error in errors))

    def test_log_tail_reads_only_the_recent_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.log"
            path.write_text("old line " + "x" * 50000 + "\nlatest line\n")
            result = server.tail(path, 100)
        self.assertIn("latest line", result)
        self.assertNotIn("old line", result)

    def test_launch_rejects_unverified_listener_on_ric_port(self):
        manager = server.Dashboard()
        config = dict(server.DEFAULT, gnbs=1, ues=1, kpmMonitor=False,
                      enableControl=False, runUntilStopped=False)
        with patch.object(server, "sctp_listener_inode", return_value="321"), \
                patch.object(server, "sctp_socket_owner", return_value=(4321, "unrelated-service")):
            with self.assertRaisesRegex(RuntimeError, "could not be verified as the original FlexRIC"):
                manager.launch(config)
        self.assertEqual(manager.processes, {})


if __name__ == "__main__":
    unittest.main()
