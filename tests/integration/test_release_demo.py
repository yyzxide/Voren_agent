from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("agentdojo"), "AgentDojo is unavailable")
class ReleaseDemoTest(unittest.TestCase):
    def test_documented_action_demo_reaches_approval_and_verified_utility(self):
        repository = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "scripts/demo_agent_loop.py"], cwd=repository,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("commit attempts before approval: 0", result.stdout)
        self.assertIn("receipt: verified", result.stdout)
        self.assertIn("AgentDojo user_task_18 utility: True", result.stdout)


if __name__ == "__main__":
    unittest.main()
