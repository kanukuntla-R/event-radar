"""V0.10D release-blocker test: proves Gunicorn's configured access-log
format cannot leak a private calendar token into its access log.

Parses the ACTUAL --access-logformat out of the real deployment unit
(deploy/systemd/event-radar-web.service) so this test validates the real
configured format, not a hand-copied duplicate that could drift. Spins up
an ISOLATED, local Gunicorn instance serving a tiny throwaway Flask app
(nothing to do with the real app.py/data/events.db) on a free localhost
port, issues a GET request whose path contains a clearly synthetic fake
token, and asserts that string never appears in the captured access log.

Requires the `gunicorn` console script to be on PATH (already a project
dependency - see requirements.txt). Uses only the stdlib plus that
subprocess.

Run with: python -m unittest test_gunicorn_access_log -v
"""
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

SERVICE_FILE = Path(__file__).parent / "deploy" / "systemd" / "event-radar-web.service"
FAKE_TOKEN = "TEST_CALENDAR_TOKEN_DO_NOT_STORE_12345"

WSGI_APP_SOURCE = """
from flask import Flask
app = Flask(__name__)

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def catch_all(path):
    return "ok"
"""


def _extract_access_logformat():
    text = SERVICE_FILE.read_text()
    exec_start = next(line for line in text.splitlines() if line.strip().startswith("ExecStart="))
    match = re.search(r"--access-logformat\s+'([^']*)'", exec_start)
    if not match:
        raise AssertionError("deploy/systemd/event-radar-web.service has no --access-logformat flag")
    return match.group(1)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class GunicornAccessLogTests(unittest.TestCase):
    def test_synthetic_token_absent_from_access_log(self):
        access_logformat = _extract_access_logformat()
        port = _free_port()

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "wsgi_test_app.py").write_text(WSGI_APP_SOURCE)

            proc = subprocess.Popen(
                [
                    sys.executable, "-m", "gunicorn",
                    "--bind", f"127.0.0.1:{port}",
                    "--workers", "1",
                    "--access-logfile", "-",
                    "--access-logformat", access_logformat,
                    "wsgi_test_app:app",
                ],
                cwd=tmpdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            try:
                self._wait_for_port(port, proc)
                # Token appears in both the path and the query string - the
                # format must not leak it through either atom.
                url = f"http://127.0.0.1:{port}/calendar/{FAKE_TOKEN}.ics?also={FAKE_TOKEN}"
                with urllib.request.urlopen(url, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                time.sleep(0.3)  # let the access log line actually get written
            finally:
                proc.terminate()
                try:
                    output, _ = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    output, _ = proc.communicate()

        self.assertNotIn(FAKE_TOKEN, output, f"synthetic token leaked into access log:\n{output}")
        # Sanity check the format actually produced a log line at all -
        # otherwise an empty/broken format would trivially "pass" above.
        self.assertIn(" GET ", output)
        self.assertIn(" 200 ", output)

    def _wait_for_port(self, port, proc, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                out, _ = proc.communicate()
                raise RuntimeError(f"gunicorn exited early:\n{out}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("gunicorn never started listening")


if __name__ == "__main__":
    unittest.main()
