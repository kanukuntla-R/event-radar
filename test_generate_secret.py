"""Tests for deploy/generate-secret.sh (V0.10C). Runs the real script as a
subprocess against an isolated temporary $HOME - never touches the real
~/.config/event-radar/secret.env, and never asserts on or prints the
generated secret's actual value (only its format/permissions). Stdlib
unittest + subprocess only.

Run with: python -m unittest test_generate_secret -v
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "deploy" / "generate-secret.sh"
SECRET_LINE_RE = re.compile(r"^EVENT_RADAR_SECRET_KEY=[0-9a-f]{64}\n$")


class GenerateSecretTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self.secret_dir = self.home / ".config" / "event-radar"
        self.secret_file = self.secret_dir / "secret.env"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run(self):
        return subprocess.run(
            ["bash", str(SCRIPT)], env={**os.environ, "HOME": str(self.home)},
            capture_output=True, text=True,
        )

    def test_creates_file_with_expected_format(self):
        result = self._run()
        self.assertEqual(result.returncode, 0)
        content = self.secret_file.read_text()
        self.assertRegex(content, SECRET_LINE_RE)

    def test_file_mode_is_0600(self):
        self._run()
        mode = self.secret_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_directory_mode_is_0700(self):
        self._run()
        mode = self.secret_dir.stat().st_mode & 0o777
        self.assertEqual(mode, 0o700)

    def test_does_not_print_secret_value(self):
        result = self._run()
        content = self.secret_file.read_text()
        value = content.strip().split("=", 1)[1]
        self.assertNotIn(value, result.stdout)
        self.assertNotIn(value, result.stderr)

    def test_rerun_preserves_existing_secret_exactly(self):
        self._run()
        first_content = self.secret_file.read_text()
        result = self._run()
        second_content = self.secret_file.read_text()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(first_content, second_content)

    def test_rerun_reports_not_rotating_without_printing_value(self):
        self._run()
        result = self._run()
        self.assertIn("already exists", result.stdout)
        self.assertIn("not rotating", result.stdout)

    def test_two_independent_installs_get_different_secrets(self):
        self._run()
        first = self.secret_file.read_text()

        other_tmpdir = tempfile.TemporaryDirectory()
        try:
            other_home = Path(other_tmpdir.name)
            subprocess.run(
                ["bash", str(SCRIPT)], env={**os.environ, "HOME": str(other_home)},
                capture_output=True, text=True, check=True,
            )
            second = (other_home / ".config" / "event-radar" / "secret.env").read_text()
        finally:
            other_tmpdir.cleanup()

        self.assertNotEqual(first, second)

    def test_idempotent_directory_permissions_across_reruns(self):
        self._run()
        self._run()
        mode = self.secret_dir.stat().st_mode & 0o777
        self.assertEqual(mode, 0o700)


if __name__ == "__main__":
    unittest.main()
