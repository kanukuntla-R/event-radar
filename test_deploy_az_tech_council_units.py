"""Static tests for the new Arizona Technology Council systemd units
(deploy/systemd/event-radar-az-tech-council.{service,timer}). No systemd
calls, no subprocess, no network, no DB - just parses the actual unit/
installer text on disk, same convention as test_gunicorn_access_log.py's
_extract_access_logformat() (validates the real deployed config, not a
hand-copied duplicate that could drift).

Run with: python -m unittest test_deploy_az_tech_council_units -v
"""
import re
import unittest
from pathlib import Path

SYSTEMD_DIR = Path(__file__).parent / "deploy" / "systemd"
SERVICE_FILE = SYSTEMD_DIR / "event-radar-az-tech-council.service"
TIMER_FILE = SYSTEMD_DIR / "event-radar-az-tech-council.timer"
INSTALLER_FILE = Path(__file__).parent / "deploy" / "install-user-units.sh"

OTHER_MAINTENANCE_SERVICES = (
    "event-radar-hackathons.service",
    "event-radar-asu-career.service",
    "event-radar-asu-events.service",
    "event-radar-source-recovery.service",
)


def _exec_start(service_path):
    text = service_path.read_text()
    line = next(l for l in text.splitlines() if l.strip().startswith("ExecStart="))
    return line.split("=", 1)[1].strip()


class ServiceFileTests(unittest.TestCase):
    def test_service_file_exists(self):
        self.assertTrue(SERVICE_FILE.exists())

    def test_execstart_invokes_discovery_script_with_persist(self):
        exec_start = _exec_start(SERVICE_FILE)
        self.assertIn("run_az_tech_council_discovery.py", exec_start)
        self.assertIn("--persist", exec_start)
        # --persist must actually be a trailing argument to the script, not
        # merely present somewhere in the line (e.g. inside a path).
        self.assertTrue(exec_start.rstrip().endswith("--persist"))

    def test_execstart_does_not_pass_dry_run_or_zero_args(self):
        exec_start = _exec_start(SERVICE_FILE)
        self.assertNotIn("--dry-run", exec_start)

    def test_execstart_uses_shared_maintenance_flock(self):
        exec_start = _exec_start(SERVICE_FILE)
        self.assertIn("/usr/bin/flock -n %t/event-radar-maintenance.lock", exec_start)

    def test_execstart_uses_venv_python_absolute_path(self):
        exec_start = _exec_start(SERVICE_FILE)
        self.assertIn("/home/kanukuntla-r/workspace/event-radar/.venv/bin/python", exec_start)

    def test_is_oneshot_like_other_maintenance_services(self):
        text = SERVICE_FILE.read_text()
        self.assertIn("Type=oneshot", text)

    def test_working_directory_matches_repo(self):
        text = SERVICE_FILE.read_text()
        self.assertIn("WorkingDirectory=/home/kanukuntla-r/workspace/event-radar", text)

    def test_flock_matches_every_other_maintenance_service(self):
        # Every existing maintenance service's ExecStart shares this exact
        # flock invocation - the new one must match it precisely so it's
        # actually part of the shared-lock group, not a look-alike.
        expected_flock = "/usr/bin/flock -n %t/event-radar-maintenance.lock"
        new_exec_start = _exec_start(SERVICE_FILE)
        for name in OTHER_MAINTENANCE_SERVICES:
            other_exec_start = _exec_start(SYSTEMD_DIR / name)
            with self.subTest(other=name):
                self.assertIn(expected_flock, other_exec_start)
        self.assertIn(expected_flock, new_exec_start)


class TimerFileTests(unittest.TestCase):
    def test_timer_file_exists(self):
        self.assertTrue(TIMER_FILE.exists())

    def test_runs_four_times_daily_every_six_hours(self):
        text = TIMER_FILE.read_text()
        calendars = re.findall(r"OnCalendar=\*-\*-\* (\d{2}:\d{2}:\d{2})", text)
        self.assertEqual(calendars, ["03:00:00", "09:00:00", "15:00:00", "21:00:00"])

    def test_does_not_collide_with_any_existing_timer_clock_time(self):
        existing_times = {
            "00:15", "06:15", "12:15", "18:15",  # asu-career
            "00:45", "06:45", "12:45", "18:45",  # asu-events
            "07:00",  # hackathons
            "07:30",  # source-recovery
        }
        text = TIMER_FILE.read_text()
        new_times = {hhmmss[:5] for hhmmss in re.findall(r"OnCalendar=\*-\*-\* (\d{2}:\d{2}:\d{2})", text)}
        self.assertTrue(new_times, "no OnCalendar lines found")
        self.assertEqual(new_times & existing_times, set())

    def test_has_persistent_and_randomized_delay_like_other_timers(self):
        text = TIMER_FILE.read_text()
        self.assertIn("Persistent=true", text)
        self.assertIn("RandomizedDelaySec=600", text)
        self.assertIn("AccuracySec=1min", text)

    def test_wanted_by_timers_target(self):
        text = TIMER_FILE.read_text()
        self.assertIn("WantedBy=timers.target", text)


class InstallerCoverageTests(unittest.TestCase):
    def test_installer_glob_would_pick_up_new_unit_files(self):
        text = INSTALLER_FILE.read_text()
        match = re.search(r'units=\(([^)]*)\)', text)
        self.assertIsNotNone(match, "could not find the units=(...) glob array in install-user-units.sh")
        glob_exprs = match.group(1).split()
        service_glob = next(g for g in glob_exprs if g.endswith(".service"))
        timer_glob = next(g for g in glob_exprs if g.endswith(".timer"))
        self.assertTrue(SERVICE_FILE.match(service_glob.split("/")[-1]))
        self.assertTrue(TIMER_FILE.match(timer_glob.split("/")[-1]))


class ExistingUnitsUnaffectedTests(unittest.TestCase):
    """Guard against this task accidentally touching any pre-existing unit
    file or the installer's mechanism while adding the new source."""

    def test_other_maintenance_services_still_use_no_args(self):
        # The four pre-existing maintenance scripts still persist on zero
        # arguments (see deploy/systemd/README.md) - only the new AZ Tech
        # Council service should carry an explicit CLI argument.
        for name in OTHER_MAINTENANCE_SERVICES:
            exec_start = _exec_start(SYSTEMD_DIR / name)
            with self.subTest(service=name):
                self.assertNotIn("--persist", exec_start)
                self.assertNotIn("--dry-run", exec_start)

    def test_installer_still_installs_web_service(self):
        text = INSTALLER_FILE.read_text()
        self.assertIn("event-radar-*.service", text)
        self.assertIn("event-radar-*.timer", text)


if __name__ == "__main__":
    unittest.main()
