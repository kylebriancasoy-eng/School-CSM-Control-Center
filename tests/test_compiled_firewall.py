from __future__ import annotations

import base64
import subprocess
import unittest
from unittest.mock import patch

from school_csm_control_center.windows_admin import (
    _encoded_powershell,
    _firewall_powershell,
    run_firewall_script,
)


class CompiledFirewallTests(unittest.TestCase):
    def test_rules_are_embedded_and_restricted_to_private_local_subnet(self) -> None:
        script = _firewall_powershell(8765, 8080, True)
        self.assertIn("protocol=TCP localport=8765", script)
        self.assertIn("protocol=TCP localport=8080", script)
        self.assertIn("protocol=UDP localport=53", script)
        self.assertIn("profile=private remoteip=localsubnet", script)
        self.assertNotIn("ALLOW_SCHOOL_CSM_FIREWALL", script)

        encoded = _encoded_powershell(script)
        self.assertEqual(base64.b64decode(encoded).decode("utf-16-le"), script)

    @patch("school_csm_control_center.windows_admin.subprocess.run")
    @patch("school_csm_control_center.windows_admin.is_administrator", return_value=True)
    @patch("school_csm_control_center.windows_admin.is_windows", return_value=True)
    def test_compiled_flow_invokes_hidden_powershell_without_cmd_file(
        self,
        _windows,
        _admin,
        run,
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        result = run_firewall_script("unused", 8765, 8080, captive_portal=False)
        self.assertTrue(result.ok)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-EncodedCommand", command)
        self.assertNotIn("cmd.exe", command)
        self.assertFalse(any(str(value).casefold().endswith(".cmd") for value in command))

    @patch("school_csm_control_center.windows_admin.is_windows", return_value=True)
    def test_invalid_port_fails_before_elevation(self, _windows) -> None:
        result = run_firewall_script("unused", 70000, 8080)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "Invalid")


if __name__ == "__main__":
    unittest.main()
