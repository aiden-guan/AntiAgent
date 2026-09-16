"""Unit tests for context-aware Auto-Review (OpenAI-style Approve for Me) engine."""

import unittest
from antiagent.config import AntiAgentConfig
from antiagent.constants import DECISION_ALLOW, DECISION_ASK, DECISION_DENY
from antiagent.engine.context_extractor import TaskContext
from antiagent.engine.evaluator import AntiAgentEvaluator


class TestAutoReview(unittest.TestCase):
    def setUp(self):
        self.config = AntiAgentConfig(profile="balanced", auto_review=True, provider="native")
        self.evaluator = AntiAgentEvaluator(self.config, workspace_paths=["/Users/test/my-project"])

    def test_coherent_test_execution_allowed(self):
        ctx = TaskContext(
            user_prompt="Add unit tests and run verification suite",
            primary_goal="Add unit tests and run verification suite",
        )
        res = self.evaluator.evaluate("run_command", {"CommandLine": "pytest tests/test_api.py"}, context=ctx)
        self.assertEqual(res.decision, DECISION_ALLOW)
        self.assertIn("Auto-approved", res.reason)

    def test_coherent_package_installation_allowed(self):
        ctx = TaskContext(
            user_prompt="Install fastapi dependencies and setup project",
            primary_goal="Install fastapi dependencies and setup project",
        )
        res = self.evaluator.evaluate("run_command", {"CommandLine": "pip install fastapi uvicorn"}, context=ctx)
        self.assertEqual(res.decision, DECISION_ALLOW)
        self.assertIn("Auto-approved", res.reason)

    def test_contextual_anomaly_unprompted_process_kill(self):
        ctx = TaskContext(
            user_prompt="Fix navbar CSS alignment and typography",
            primary_goal="Fix navbar CSS alignment and typography",
        )
        # Agent proposing to killall python during a CSS edit task
        res = self.evaluator.evaluate("run_command", {"CommandLine": "killall python"}, context=ctx)
        self.assertEqual(res.decision, DECISION_ASK)
        self.assertIn("Contextual Anomaly", res.reason)

    def test_contextual_anomaly_unprompted_package_install(self):
        ctx = TaskContext(
            user_prompt="Fix typo in README.md documentation",
            primary_goal="Fix typo in README.md documentation",
        )
        # Agent proposing pip install during a README typo fix
        res = self.evaluator.evaluate("run_command", {"CommandLine": "pip install requests"}, context=ctx)
        self.assertEqual(res.decision, DECISION_ASK)
        self.assertIn("out-of-scope", res.reason)

    def test_coherent_file_mutation_allowed(self):
        ctx = TaskContext(
            user_prompt="Create user authentication module",
            primary_goal="Create user authentication module",
        )
        res = self.evaluator.evaluate(
            "write_to_file",
            {"TargetFile": "/Users/test/my-project/src/auth.py", "CodeContent": "# auth"},
            context=ctx,
        )
        self.assertEqual(res.decision, DECISION_ALLOW)

    def test_vulnerability_blocked_regardless_of_context(self):
        ctx = TaskContext(
            user_prompt="Setup server dependencies",
            primary_goal="Setup server dependencies",
        )
        # Disabling SSL verification is caught by VulnerabilityGuard
        res = self.evaluator.evaluate("run_command", {"CommandLine": "curl --insecure https://packages.org/pkg.tar.gz"}, context=ctx)
        self.assertEqual(res.decision, DECISION_ASK)
        self.assertIn("Insecure TLS bypass", res.reason)

    def test_autonomous_mode_decisive_approve_or_deny(self):
        auto_cfg = AntiAgentConfig(profile="autonomous", auto_review=True, provider="native")
        evaluator = AntiAgentEvaluator(auto_cfg, workspace_paths=["/Users/test/my-project"])

        # 1. Coherent command is auto-approved (ALLOW)
        ctx_ok = TaskContext(user_prompt="Install fastapi", primary_goal="Install fastapi")
        res_ok = evaluator.evaluate("run_command", {"CommandLine": "pip install fastapi"}, context=ctx_ok)
        self.assertEqual(res_ok.decision, DECISION_ALLOW)

        # 2. Anomaly is decisively denied (DENY) without prompting user
        ctx_anomaly = TaskContext(user_prompt="Fix CSS", primary_goal="Fix CSS")
        res_anomaly = evaluator.evaluate("run_command", {"CommandLine": "killall python"}, context=ctx_anomaly)
        self.assertEqual(res_anomaly.decision, DECISION_DENY)
        self.assertIn("Autonomous Block", res_anomaly.reason)

        # 3. Security risk (insecure TLS) is decisively denied (DENY) without prompting user
        res_vuln = evaluator.evaluate("run_command", {"CommandLine": "curl --insecure https://pkg.org"}, context=ctx_ok)
        self.assertEqual(res_vuln.decision, DECISION_DENY)
        self.assertIn("Autonomous Block", res_vuln.reason)

    def test_rm_rf_disposable_build_cache_auto_approved(self):
        # Cleaning disposable caches in balanced mode when rebuilding
        ctx = TaskContext(user_prompt="Clean and rebuild project artifacts", primary_goal="Rebuild project")
        res = self.evaluator.evaluate("run_command", {"CommandLine": "rm -rf dist build node_modules"}, context=ctx)
        self.assertEqual(res.decision, DECISION_ALLOW)
        self.assertIn("Auto-approved", res.reason)

        # Autonomous mode auto-approves disposable cache deletions without prompt
        auto_cfg = AntiAgentConfig(profile="autonomous", auto_review=True, provider="native")
        evaluator = AntiAgentEvaluator(auto_cfg, workspace_paths=["/Users/test/my-project"])
        res_auto = evaluator.evaluate("run_command", {"CommandLine": "rm -rf node_modules"}, context=ctx)
        self.assertEqual(res_auto.decision, DECISION_ALLOW)

    def test_rm_rf_source_directory_anomaly_blocked(self):
        # Trying to rm -rf src/ during a CSS task
        ctx = TaskContext(user_prompt="Fix navbar CSS fonts", primary_goal="Fix navbar CSS fonts")
        auto_cfg = AntiAgentConfig(profile="autonomous", auto_review=True, provider="native")
        evaluator = AntiAgentEvaluator(auto_cfg, workspace_paths=["/Users/test/my-project"])

        res = evaluator.evaluate("run_command", {"CommandLine": "rm -rf src/"}, context=ctx)
        self.assertEqual(res.decision, DECISION_DENY)
        self.assertIn("Autonomous Block", res.reason)

    def test_sudo_package_management_deep_inspection(self):
        # In autonomous mode, safe dev admin task (apt install) is auto-approved when coherent
        auto_cfg = AntiAgentConfig(profile="autonomous", auto_review=True, provider="native")
        evaluator = AntiAgentEvaluator(auto_cfg, workspace_paths=["/Users/test/my-project"])
        ctx = TaskContext(user_prompt="Setup linux system packages for build", primary_goal="Setup system packages")
        res = evaluator.evaluate("run_command", {"CommandLine": "sudo apt-get install -y libpq-dev"}, context=ctx)
        self.assertEqual(res.decision, DECISION_ALLOW)
        self.assertIn("Auto-approved", res.reason)

    def test_sudo_catastrophic_threat_hard_denied(self):
        # Catastrophic threats under sudo are still strictly hard-denied in all modes
        auto_cfg = AntiAgentConfig(profile="autonomous", auto_review=True, provider="native")
        evaluator = AntiAgentEvaluator(auto_cfg, workspace_paths=["/Users/test/my-project"])
        ctx = TaskContext(user_prompt="Clean up system files", primary_goal="Clean up system")

        res_root = evaluator.evaluate("run_command", {"CommandLine": "sudo rm -rf /"}, context=ctx)
        self.assertEqual(res_root.decision, DECISION_DENY)

        res_shadow = evaluator.evaluate("run_command", {"CommandLine": "sudo cat /etc/shadow"}, context=ctx)
        self.assertEqual(res_shadow.decision, DECISION_DENY)


    def test_script_eval_autonomous_auto_approved(self):
        auto_cfg = AntiAgentConfig(profile="autonomous", auto_review=True, provider="native")
        evaluator = AntiAgentEvaluator(auto_cfg, workspace_paths=["/Users/test/my-project"])
        cmd = "python3 -c \"import subprocess; print(subprocess.run(['osascript', '-e', 'return POSIX path of (path to home folder)'], capture_output=True, text=True).stdout.strip())\""
        res = evaluator.evaluate("run_command", {"CommandLine": cmd})
        self.assertEqual(res.decision, DECISION_ALLOW)
        self.assertIn("Auto-approved", res.reason)

    def test_script_eval_balanced_with_intent(self):
        ctx = TaskContext(user_prompt="Choose project folder via dialog", primary_goal="Select folder dialog")
        cmd = "python3 -c \"import subprocess; print(subprocess.run(['osascript', '-e', 'choose folder'], capture_output=True, text=True).stdout.strip())\""
        res = self.evaluator.evaluate("run_command", {"CommandLine": cmd}, context=ctx)
        self.assertEqual(res.decision, DECISION_ALLOW)
        self.assertIn("Auto-approved", res.reason)


if __name__ == "__main__":
    unittest.main()
