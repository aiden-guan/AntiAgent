"""Comprehensive tests for Remote Sessions via SSH."""

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from antiagent.engine.remote_sessions import (
    AntigravityStatus,
    AntiAgentRemoteStatus,
    CommandResult,
    RemoteControlStatus,
    RemoteHost,
    RemoteHostRegistry,
    RemoteProbeResult,
    RemoteSessionManager,
    SSHClient,
    build_ssh_argv,
    classify_ssh_error,
    is_safe_antigravity_url,
    parse_antiagent_status,
    parse_probe_output,
    parse_remote_control_status,
    quote_posix_arg,
    quote_windows_arg,
    validate_identity_file_path,
    validate_instance_name,
    validate_remote_name,
    validate_ssh_host,
    validate_workspace_path,
)


class TestRemoteSessions(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.registry_file = Path(self.test_dir) / "remotes.json"
        self.registry = RemoteHostRegistry(self.registry_file)
        self.audit_log_file = Path(self.test_dir) / "audit.log"
        self.manager = RemoteSessionManager(
            registry=self.registry,
            audit_log_path=str(self.audit_log_file),
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. Validation & Security Checks
    def test_validate_remote_name_valid(self):
        for valid in ["mac-mini", "devbox_1", "linux.server", "worker-node-42"]:
            validate_remote_name(valid)

    def test_validate_remote_name_invalid(self):
        for invalid in ["", "-leading-dash", "-v", "mac mini", "box;rm", "server$(whoami)", "host/path", "name\n"]:
            with self.assertRaises(ValueError):
                validate_remote_name(invalid)

    def test_validate_ssh_host_rejects_injection(self):
        validate_ssh_host("mac-mini")
        validate_ssh_host("192.168.1.50")
        validate_ssh_host("my-ssh-alias")

        for hostile in [
            "-oProxyCommand=calc",
            "-v",
            "server; echo pwned",
            "server&&echo pwned",
            "server|echo pwned",
            "server`whoami`",
            "server$(whoami)",
            "server\nwhoami",
            "server'test",
            'server"test',
            "server>file",
        ]:
            with self.assertRaises(ValueError):
                validate_ssh_host(hostile)

    def test_validate_instance_name(self):
        validate_instance_name("Aiden Mac Mini")
        validate_instance_name("devbox_01-test")
        validate_instance_name(None)

        for hostile in ["mac; touch /tmp/pwn", "box\nname", "dev`whoami`", "box$ENV", 'box"name']:
            with self.assertRaises(ValueError):
                validate_instance_name(hostile)

    def test_validate_workspace_path(self):
        validate_workspace_path("~/Developer/PigeonBox")
        validate_workspace_path("/home/ubuntu/project")
        validate_workspace_path(None)

        for hostile in ["~/code\nrm -rf /", "/tmp/path\0something"]:
            with self.assertRaises(ValueError):
                validate_workspace_path(hostile)

    def test_validate_identity_file_rejects_private_key_contents(self):
        validate_identity_file_path("~/.ssh/id_ed25519")
        validate_identity_file_path("/etc/ssl/id_rsa")
        validate_identity_file_path(None)

        with self.assertRaises(ValueError):
            validate_identity_file_path("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1...")
        with self.assertRaises(ValueError):
            validate_identity_file_path("-----BEGIN RSA PRIVATE KEY-----")

    def test_remote_host_bans_secret_fields(self):
        data = {
            "name": "devbox",
            "ssh_host": "devbox.internal",
            "password": "super-secret-password",
            "private_key": "private-key-data",
            "token": "secret-oauth-token",
        }
        host = RemoteHost.from_dict(data)
        serialized = host.to_dict()
        self.assertNotIn("password", serialized)
        self.assertNotIn("private_key", serialized)
        self.assertNotIn("token", serialized)

    # 2. Registry Operations
    def test_registry_add_and_get_host(self):
        host = RemoteHost(
            name="mac-mini",
            ssh_host="mac-mini.local",
            user="aiden",
            port=2222,
            workspace="~/Developer/PigeonBox",
            antigravity_name="Aiden Mac Mini",
        )
        self.registry.add_host(host)

        loaded = self.registry.get_host("mac-mini")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "mac-mini")
        self.assertEqual(loaded.ssh_host, "mac-mini.local")
        self.assertEqual(loaded.user, "aiden")
        self.assertEqual(loaded.port, 2222)
        self.assertEqual(loaded.workspace, "~/Developer/PigeonBox")
        self.assertEqual(loaded.antigravity_name, "Aiden Mac Mini")

    def test_registry_duplicate_name_error(self):
        host1 = RemoteHost(name="devbox", ssh_host="devbox1")
        host2 = RemoteHost(name="devbox", ssh_host="devbox2")
        self.registry.add_host(host1)
        with self.assertRaises(ValueError):
            self.registry.add_host(host2)

    def test_registry_remove_host(self):
        host = RemoteHost(name="devbox", ssh_host="devbox")
        self.registry.add_host(host)
        self.assertTrue(self.registry.remove_host("devbox"))
        self.assertIsNone(self.registry.get_host("devbox"))
        self.assertFalse(self.registry.remove_host("nonexistent"))

    def test_registry_atomic_write_and_permissions(self):
        host = RemoteHost(name="secure-host", ssh_host="10.0.0.1")
        path = self.registry.add_host(host)
        self.assertTrue(self.registry_file.is_file())

        if hasattr(os, "stat") and sys.platform != "win32":
            mode = stat.S_IMODE(os.stat(self.registry_file).st_mode)
            self.assertEqual(mode, 0o600)

    def test_registry_handles_malformed_json_gracefully(self):
        self.registry_file.write_text("{corrupt json !!!", encoding="utf-8")
        hosts = self.registry.load()
        self.assertEqual(hosts, {})

    # 3. SSH Command Line Generation & Security
    def test_build_ssh_argv_non_interactive(self):
        host = RemoteHost(
            name="box1",
            ssh_host="devbox.local",
            user="ubuntu",
            port=2202,
            identity_file="~/.ssh/my_key",
        )
        cmd = build_ssh_argv(host, remote_command=["uname", "-s"], interactive=False)

        self.assertEqual(cmd[0], "ssh")
        self.assertIn("-o", cmd)
        self.assertIn("BatchMode=yes", cmd)
        self.assertIn("-p", cmd)
        self.assertIn("2202", cmd)
        self.assertIn("-i", cmd)
        self.assertIn(os.path.expanduser("~/.ssh/my_key"), cmd)
        self.assertIn("ubuntu@devbox.local", cmd)
        self.assertEqual(cmd[-1], "uname -s")

        # Security: NEVER StrictHostKeyChecking=no
        cmd_str = " ".join(cmd)
        self.assertNotIn("StrictHostKeyChecking=no", cmd_str)

    def test_build_ssh_argv_interactive(self):
        host = RemoteHost(name="box2", ssh_host="my-alias")
        cmd = build_ssh_argv(host, remote_command=["agy", "--remote-control"], interactive=True)
        self.assertIn("-t", cmd)
        self.assertNotIn("BatchMode=yes", cmd)
        self.assertIn("my-alias", cmd)
        self.assertEqual(cmd[-1], "agy --remote-control")

    def test_quote_posix_arg_prevents_injection(self):
        malicious = "repo; rm -rf /"
        quoted = quote_posix_arg(malicious)
        self.assertEqual(quoted, "'repo; rm -rf /'")

    def test_quote_windows_arg(self):
        arg = 'test "quoted" value'
        quoted = quote_windows_arg(arg)
        self.assertEqual(quoted, '"test ""quoted"" value"')

    # 4. Error Classification
    def test_classify_ssh_error(self):
        # Auth failure
        t, msg = classify_ssh_error(255, "", "Permission denied (publickey,password).", False)
        self.assertEqual(t, "auth_failed")
        self.assertIn("SSH authentication failed", msg)

        # Host key failure
        t, msg = classify_ssh_error(255, "", "Host key verification failed.", False)
        self.assertEqual(t, "host_key_failed")
        self.assertIn("Host key verification failed", msg)

        # Changed host key
        t, msg = classify_ssh_error(255, "", "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@", False)
        self.assertEqual(t, "host_key_failed")

        # Unreachable
        t, msg = classify_ssh_error(255, "", "ssh: Could not resolve hostname foo: nodename nor servname provided", False)
        self.assertEqual(t, "unreachable")

        t, msg = classify_ssh_error(255, "", "ssh: connect to host 10.0.0.99 port 22: Connection refused", False)
        self.assertEqual(t, "unreachable")

        # Timeout
        t, msg = classify_ssh_error(124, "", "Timed out", True)
        self.assertEqual(t, "timeout")

    # 5. Status Parsers
    def test_parse_remote_control_status_running(self):
        output = """
        Google Antigravity Remote Control Daemon
        Status: Running
        Instance Name: Mac-Mini-Aiden
        URL: https://antigravity.google.com/r/xyz123abc
        """
        status = parse_remote_control_status(output, "", 0)
        self.assertTrue(status.running)
        self.assertTrue(status.supported)
        self.assertEqual(status.instance_name, "Mac-Mini-Aiden")
        self.assertEqual(status.url, "https://antigravity.google.com/r/xyz123abc")

    def test_parse_remote_control_status_stopped(self):
        output = "Remote control is not running."
        status = parse_remote_control_status(output, "", 1)
        self.assertFalse(status.running)
        self.assertTrue(status.supported)
        self.assertTrue(status.authenticated)

    def test_parse_remote_control_status_auth_required(self):
        output = "Authentication required. Please run: agy auth login"
        status = parse_remote_control_status(output, "", 1)
        self.assertFalse(status.authenticated)
        self.assertFalse(status.running)

    def test_parse_remote_control_status_unknown(self):
        output = "Some totally unexpected greeting output."
        status = parse_remote_control_status(output, "", 0)
        self.assertIsNone(status.running)
        self.assertTrue(status.authenticated)

    def test_parse_remote_control_status_unsupported(self):
        output = "agy: error: unrecognized arguments: remote-control"
        status = parse_remote_control_status(output, "", 2)
        self.assertFalse(status.supported)
        self.assertFalse(status.running)

    def test_parse_antiagent_status(self):
        # Protected
        out_active = """
        🛡️  AntiAgent v0.2.0 Status
        =============================================
        Workspace Hook: ⚪ Inactive (/repo/.agents/hooks.json)
        Global Hook:    🟢 Active (/Users/aiden/.gemini/config/hooks.json)
        """
        st1 = parse_antiagent_status(out_active, "", 0)
        self.assertTrue(st1.installed)
        self.assertEqual(st1.version, "0.2.0")
        self.assertTrue(st1.global_hook_active)
        self.assertEqual(st1.protection_status, "Protected")

        # Disabled hook
        out_disabled = """
        🛡️  AntiAgent v0.2.0 Status
        Workspace Hook: ⚪ Inactive
        Global Hook:    ⚪ Inactive
        """
        st2 = parse_antiagent_status(out_disabled, "", 0)
        self.assertTrue(st2.installed)
        self.assertFalse(st2.global_hook_active)
        self.assertEqual(st2.protection_status, "AntiAgent installed but hook disabled")

        # Not installed
        st3 = parse_antiagent_status("", "sh: antiagent: command not found", 127)
        self.assertFalse(st3.installed)
        self.assertEqual(st3.protection_status, "AntiAgent not installed")

    # 6. URL Validation
    def test_is_safe_antigravity_url(self):
        self.assertTrue(is_safe_antigravity_url("https://antigravity.google.com/"))
        self.assertTrue(is_safe_antigravity_url("https://antigravity.google.com/r/session123"))
        self.assertTrue(is_safe_antigravity_url("https://sub.antigravity.google.com/"))

        self.assertFalse(is_safe_antigravity_url("http://antigravity.google.com/"))  # No HTTP
        self.assertFalse(is_safe_antigravity_url("https://evil.com/"))
        self.assertFalse(is_safe_antigravity_url("https://antigravity.google.com.evil.com/"))
        self.assertFalse(is_safe_antigravity_url("javascript:alert(1)"))

    # 7. Probe Parsing
    def test_parse_probe_output_full(self):
        probe_stdout = """
---AA_PROBE---
OS:Darwin
HOST:aiden-mac-mini
WS:EXISTS:/Users/aiden/Developer/PigeonBox
AGY_PATH:/Users/aiden/.local/bin/agy
AGY_VER:agy version 1.15.0
---AGY_STATUS_START---
Status: Running
Name: Aiden-Mini
---AGY_STATUS_END---
AA_PATH:/Users/aiden/.local/bin/antiagent
AA_VER:antiagent 0.2.0
---AA_STATUS_START---
Global Hook:    🟢 Active
---AA_STATUS_END---
---AA_PROBE_END---
"""
        host = RemoteHost(
            name="mac-mini",
            ssh_host="mac-mini.local",
            workspace="~/Developer/PigeonBox",
        )
        res = parse_probe_output(probe_stdout, "", 0, host, latency_ms=45)

        self.assertTrue(res.ok)
        self.assertTrue(res.ssh_connected)
        self.assertEqual(res.remote_os, "macOS")
        self.assertEqual(res.hostname, "aiden-mac-mini")
        self.assertTrue(res.workspace_exists)
        self.assertEqual(res.workspace_path, "/Users/aiden/Developer/PigeonBox")
        self.assertTrue(res.antigravity.installed)
        self.assertEqual(res.antigravity.version, "agy version 1.15.0")
        self.assertTrue(res.remote_control.running)
        self.assertEqual(res.remote_control.instance_name, "Aiden-Mini")
        self.assertTrue(res.antiagent.installed)
        self.assertTrue(res.antiagent.global_hook_active)
        self.assertEqual(res.antiagent.protection_status, "Protected")

    def test_parse_probe_output_auth_required(self):
        probe_stdout = """
---AA_PROBE---
OS:Linux
HOST:ubuntu-server
WS:EXISTS:/home/ubuntu/app
AGY_PATH:/usr/local/bin/agy
AGY_VER:agy version 1.14.0
---AGY_STATUS_START---
Authentication required. Please run: agy auth login
---AGY_STATUS_END---
AA_PATH:
AA_VER:
---AA_PROBE_END---
"""
        host = RemoteHost(name="server", ssh_host="server.internal")
        res = parse_probe_output(probe_stdout, "", 0, host, latency_ms=80)

        self.assertTrue(res.ok)
        self.assertEqual(res.remote_os, "Linux")
        self.assertTrue(res.antigravity.installed)
        self.assertFalse(res.antigravity.authenticated)
        self.assertIn("authentication is required", res.antigravity.raw_error)
        self.assertFalse(res.antiagent.installed)
        self.assertEqual(res.antiagent.protection_status, "AntiAgent not installed")

    # 8. RemoteSessionManager Operations (Mocked Subprocess)
    @patch.object(SSHClient, "run_command")
    def test_manager_test_connection_success(self, mock_run):
        mock_run.return_value = CommandResult(
            argv=["ssh", "mac-mini", "uname", "-s"],
            returncode=0,
            stdout="Darwin\n",
            stderr="",
            duration_ms=42,
        )
        host = RemoteHost(name="mac-mini", ssh_host="mac-mini")
        self.registry.add_host(host)

        res = self.manager.test_connection("mac-mini")
        self.assertTrue(res["ok"])
        self.assertEqual(res["latency_ms"], 42)
        self.assertEqual(res["remote_os"], "macOS")

        # Verify audit log
        self.assertTrue(self.audit_log_file.is_file())
        audit_content = self.audit_log_file.read_text(encoding="utf-8")
        self.assertIn("remote_connection_test", audit_content)
        self.assertIn("mac-mini", audit_content)

    @patch.object(SSHClient, "run_command")
    def test_manager_probe_caching(self, mock_run):
        mock_run.return_value = CommandResult(
            argv=["ssh", "devbox"],
            returncode=0,
            stdout="""---AA_PROBE---
OS:Linux
HOST:devbox
WS:NONE:
AGY_PATH:/bin/agy
AGY_VER:1.0
---AA_PROBE_END---""",
            stderr="",
            duration_ms=30,
        )
        host = RemoteHost(name="devbox", ssh_host="devbox")
        self.registry.add_host(host)

        # 1. First probe: hits SSHClient
        p1 = self.manager.probe("devbox")
        self.assertFalse(p1.cached)
        self.assertEqual(mock_run.call_count, 1)

        # 2. Second probe: returns cached
        p2 = self.manager.probe("devbox")
        self.assertTrue(p2.cached)
        self.assertEqual(mock_run.call_count, 1)

        # 3. Bypass cache
        p3 = self.manager.probe("devbox", bypass_cache=True)
        self.assertFalse(p3.cached)
        self.assertEqual(mock_run.call_count, 2)

    @patch.object(SSHClient, "run_command")
    def test_manager_start_remote_control(self, mock_run):
        # 1st call: agy remote-control start
        # 2nd call: agy remote-control status
        mock_run.side_effect = [
            CommandResult(argv=[], returncode=0, stdout="Started daemon\n", stderr="", duration_ms=50),
            CommandResult(argv=[], returncode=0, stdout="Status: Running\nInstance: DevMachine\n", stderr="", duration_ms=40),
        ]
        host = RemoteHost(name="dev", ssh_host="dev", antigravity_name="DevMachine")
        self.registry.add_host(host)

        res = self.manager.start_remote_control("dev")
        self.assertTrue(res["ok"])
        self.assertTrue(res["running"])
        self.assertEqual(res["instance_name"], "DevMachine")

        # Verify audit event
        audit_content = self.audit_log_file.read_text(encoding="utf-8")
        self.assertIn("remote_control_start", audit_content)

    @patch.object(SSHClient, "run_command")
    def test_manager_stop_remote_control(self, mock_run):
        # 1st call: agy remote-control stop
        # 2nd call: agy remote-control status
        mock_run.side_effect = [
            CommandResult(argv=[], returncode=0, stdout="Stopped\n", stderr="", duration_ms=50),
            CommandResult(argv=[], returncode=1, stdout="Remote control is not running.\n", stderr="", duration_ms=40),
        ]
        host = RemoteHost(name="dev", ssh_host="dev")
        self.registry.add_host(host)

        res = self.manager.stop_remote_control("dev")
        self.assertTrue(res["ok"])
        self.assertFalse(res["running"])

        # Verify audit event
        audit_content = self.audit_log_file.read_text(encoding="utf-8")
        self.assertIn("remote_control_stop", audit_content)

    @patch.object(SSHClient, "run_command")
    def test_manager_protect_remote_not_installed_fails_cleanly(self, mock_run):
        mock_run.return_value = CommandResult(
            argv=[],
            returncode=0,
            stdout="""---AA_PROBE---
OS:Linux
HOST:srv
WS:NONE:
AGY_PATH:
AGY_VER:
AA_PATH:
AA_VER:
---AA_PROBE_END---""",
            stderr="",
            duration_ms=20,
        )
        host = RemoteHost(name="srv", ssh_host="srv")
        self.registry.add_host(host)

        res = self.manager.protect_remote("srv")
        self.assertFalse(res["ok"])
        self.assertIn("not installed", res["error"])
        self.assertIn("pip install antiagent", res["error"])

    @patch.object(SSHClient, "run_command")
    def test_manager_protect_remote_success(self, mock_run):
        mock_run.side_effect = [
            # 1. First probe: antiagent installed, hook inactive
            CommandResult(argv=[], returncode=0, stdout="""---AA_PROBE---
OS:Linux
HOST:srv
WS:NONE:
AGY_PATH:/bin/agy
AGY_VER:1.0
AA_PATH:/bin/antiagent
AA_VER:0.2.0
---AA_STATUS_START---
Global Hook: ⚪ Inactive
---AA_STATUS_END---
---AA_PROBE_END---""", stderr="", duration_ms=20),
            # 2. antiagent install --global
            CommandResult(argv=[], returncode=0, stdout="Installed", stderr="", duration_ms=50),
            # 3. Second probe: hook active
            CommandResult(argv=[], returncode=0, stdout="""---AA_PROBE---
OS:Linux
HOST:srv
WS:NONE:
AGY_PATH:/bin/agy
AGY_VER:1.0
AA_PATH:/bin/antiagent
AA_VER:0.2.0
---AA_STATUS_START---
Global Hook: 🟢 Active
---AA_STATUS_END---
---AA_PROBE_END---""", stderr="", duration_ms=20),
        ]
        host = RemoteHost(name="srv", ssh_host="srv")
        self.registry.add_host(host)

        res = self.manager.protect_remote("srv")
        self.assertTrue(res["ok"])
        self.assertEqual(res["protection_status"], "Protected")

    @patch.object(SSHClient, "run_command")
    def test_manager_probe_all(self, mock_run):
        mock_run.return_value = CommandResult(
            argv=[], returncode=0,
            stdout="""---AA_PROBE---
OS:Linux
HOST:srv
WS:NONE:
AGY_PATH:/bin/agy
AGY_VER:1.0
---AA_PROBE_END---""",
            stderr="", duration_ms=25
        )
        self.registry.add_host(RemoteHost(name="box1", ssh_host="box1"))
        self.registry.add_host(RemoteHost(name="box2", ssh_host="box2"))

        results = self.manager.probe_all()
        self.assertEqual(len(results), 2)
        names = [r.name for r in results]
        self.assertIn("box1", names)
        self.assertIn("box2", names)

    @patch.object(SSHClient, "run_command")
    def test_manager_doctor(self, mock_run):
        mock_run.return_value = CommandResult(
            argv=[], returncode=0,
            stdout="""---AA_PROBE---
OS:Darwin
HOST:mac-mini
WS:EXISTS:/workspace
AGY_PATH:/opt/homebrew/bin/agy
AGY_VER:1.15.0
---AGY_STATUS_START---
Status: Stopped
---AGY_STATUS_END---
AA_PATH:/usr/local/bin/antiagent
AA_VER:0.2.0
---AA_STATUS_START---
Global Hook: 🟢 Active
---AA_STATUS_END---
---AA_PROBE_END---""",
            stderr="", duration_ms=30
        )
        self.registry.add_host(RemoteHost(name="mac", ssh_host="mac", workspace="/workspace"))
        doc = self.manager.doctor("mac")
        self.assertTrue(doc["ok"])
        self.assertFalse(doc["has_errors"])
        self.assertIn("checks", doc)
        self.assertTrue(len(doc["checks"]) >= 5)
        check_names = [c["name"] for c in doc["checks"]]
        self.assertIn("SSH Reachability", check_names)
        self.assertIn("Google Antigravity CLI", check_names)
        self.assertIn("AntiAgent Protection Hook", check_names)
        probe = doc["probe"]
        self.assertEqual(probe["remote_os"], "macOS")
        self.assertEqual(probe["antigravity"]["version"], "1.15.0")
        self.assertEqual(probe["antiagent"]["protection_status"], "Protected")

    @patch.object(SSHClient, "run_command")
    def test_manager_start_remote_control_with_workspace(self, mock_run):
        mock_run.side_effect = [
            CommandResult(argv=[], returncode=0, stdout="Started", stderr="", duration_ms=40),
            CommandResult(argv=[], returncode=0, stdout="Status: Running\nInstance: Mini\n", stderr="", duration_ms=30),
        ]
        host = RemoteHost(name="mini", ssh_host="mini", workspace="~/dev/app", antigravity_name="Mini")
        self.registry.add_host(host)

        res = self.manager.start_remote_control("mini")
        self.assertTrue(res["ok"])
        first_call_cmd = mock_run.call_args_list[0][0][1]
        self.assertIn("cd '~/dev/app'", first_call_cmd[-1])

        # Verify audit event has category remote_management
        from antiagent.audit.logger import AuditLogger
        logger = AuditLogger(str(self.audit_log_file))
        entries = logger.read_recent(10)
        self.assertTrue(any(e.get("category") == "remote_management" for e in entries))

    def test_build_ssh_argv_windows_remote(self):
        host = RemoteHost(name="winbox", ssh_host="winbox", remote_os="windows")
        cmd = build_ssh_argv(host, remote_command=["agy", "remote-control", "start", "--name", "My Server"])
        self.assertIn('"My Server"', cmd[-1])

    @patch("antiagent.engine.remote_sessions.launch_interactive_ssh", return_value=0)
    def test_manager_connect_interactive(self, mock_launch):
        host = RemoteHost(name="dev", ssh_host="dev", workspace="~/project")
        self.registry.add_host(host)

        code = self.manager.connect_interactive("dev", no_open=True)
        self.assertEqual(code, 0)
        self.assertEqual(mock_launch.call_count, 1)
        argv = mock_launch.call_args[0][0]
        self.assertIn("-t", argv)
        self.assertIn("dev", argv)
        self.assertIn("cd '~/project' && agy --remote-control", argv[-1])

    @patch("antiagent.engine.remote_sessions.launch_interactive_ssh", return_value=0)
    def test_manager_login_interactive(self, mock_launch):
        host = RemoteHost(name="dev", ssh_host="dev")
        self.registry.add_host(host)

        with patch.object(self.manager, "probe") as mock_probe:
            mock_probe.return_value = RemoteProbeResult(
                ok=True, name="dev", ssh_connected=True,
                antigravity=AntigravityStatus(installed=True, authenticated=True)
            )
            code = self.manager.login_interactive("dev")
            self.assertEqual(code, 0)
            self.assertEqual(mock_launch.call_count, 1)


if __name__ == "__main__":
    unittest.main()

