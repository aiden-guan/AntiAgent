"""Test client-side restart verification logic directly extracted from index.html."""

import json
import re
import subprocess
import unittest
from pathlib import Path


class TestClientRestartVerification(unittest.TestCase):
    def setUp(self):
        index_html = Path(__file__).resolve().parent.parent / "antiagent" / "dashboard" / "assets" / "index.html"
        self.assertTrue(index_html.is_file())
        content = index_html.read_text(encoding="utf-8")

        # Extract parseVersionSegments, compareVersions, and evaluateRestartState functions
        parse_match = re.search(r"function parseVersionSegments\([^\)]*\)\s*\{[\s\S]*?\n    \}", content)
        comp_match = re.search(r"function compareVersions\([^\)]*\)\s*\{[\s\S]*?\n    \}", content)
        eval_match = re.search(r"function evaluateRestartState\([^\)]*\)\s*\{[\s\S]*?\n    \}", content)

        self.assertIsNotNone(parse_match, "parseVersionSegments function not found in index.html")
        self.assertIsNotNone(comp_match, "compareVersions function not found in index.html")
        self.assertIsNotNone(eval_match, "evaluateRestartState function not found in index.html")

        self.js_code = f"""
const fs = require('fs');
{parse_match.group(0)}
{comp_match.group(0)}
{eval_match.group(0)}

const testCases = JSON.parse(fs.readFileSync(0, 'utf-8'));
const results = testCases.map(tc => evaluateRestartState(tc.initial, tc.current));
console.log(JSON.stringify(results));
"""

    def _run_node_eval(self, cases):
        res = subprocess.run(
            ["node", "-e", self.js_code],
            input=json.dumps(cases),
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            self.fail(f"Node execution failed (code {res.returncode}):\n{res.stderr}\nCode:\n{self.js_code}")
        return json.loads(res.stdout)

    def test_evaluate_restart_state_all_branches(self):
        cases = [
            # 1. Server unreachable
            {
                "initial": {"version": "0.4.3", "server_instance_id": "inst-1", "target_version": "0.4.4"},
                "current": None,
            },
            # 2. Server unreachable (ok: false)
            {
                "initial": {"version": "0.4.3", "server_instance_id": "inst-1", "target_version": "0.4.4"},
                "current": {"ok": False},
            },
            # 3. Same instance (server hasn't restarted yet)
            {
                "initial": {"version": "0.4.3", "server_instance_id": "inst-1", "target_version": "0.4.4"},
                "current": {"ok": True, "version": "0.4.3", "server_instance_id": "inst-1"},
            },
            # 4. New instance but old version (e.g. failed upgrade / rollback)
            {
                "initial": {"version": "0.4.3", "server_instance_id": "inst-1", "target_version": "0.4.4"},
                "current": {"ok": True, "version": "0.4.3", "server_instance_id": "inst-2"},
            },
            # 5. Success: new instance AND expected target version
            {
                "initial": {"version": "0.4.3", "server_instance_id": "inst-1", "target_version": "0.4.4"},
                "current": {"ok": True, "version": "0.4.4", "server_instance_id": "inst-2"},
            },
            # 6. Success: target not provided, but version incremented
            {
                "initial": {"version": "0.4.3", "server_instance_id": "inst-1", "target_version": ""},
                "current": {"ok": True, "version": "0.5.0", "server_instance_id": "inst-2"},
            },
        ]

        results = self._run_node_eval(cases)

        self.assertEqual(results[0], {"success": False, "reason": "server_unreachable"})
        self.assertEqual(results[1], {"success": False, "reason": "server_unreachable"})
        self.assertEqual(results[2], {"success": False, "reason": "same_server_instance"})
        self.assertEqual(results[3], {"success": False, "reason": "version_not_updated"})
        self.assertEqual(results[4], {"success": True, "reason": "verified"})
        self.assertEqual(results[5], {"success": True, "reason": "verified"})


if __name__ == "__main__":
    unittest.main()
