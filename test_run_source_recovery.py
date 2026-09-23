"""Tests for the run_source_recovery.py CLI's safety behavior. Unlike
run_az_tech_council_discovery.py (which has no live systemd unit and
requires an explicit --dry-run/--persist), this script is invoked by
event-radar-source-recovery.timer with NO arguments daily at 07:30, so
no-argument invocation must keep running a real persistent pass
(dry_run=False) - only --help/-h and unknown arguments must stay
side-effect-free.
"""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import run_source_recovery as cli

FAKE_RESULT = {
    "events": [],
    "summary": {
        "eligible": 0, "processed": 0, "candidates_found": 0,
        "candidates_inserted": 0, "duplicates_skipped": 0, "runner_errors": 0,
    },
}


class TestArgParsing(unittest.TestCase):
    def test_help_exits_zero_no_side_effects(self):
        with patch("db.get_db") as mock_get_db, \
             patch("hackathon_source_recovery_runner.run_source_recovery") as mock_run, \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--help"])
            self.assertEqual(ctx.exception.code, 0)
        mock_get_db.assert_not_called()
        mock_run.assert_not_called()

    def test_short_help_flag_behaves_the_same(self):
        with patch("db.get_db") as mock_get_db, \
             patch("hackathon_source_recovery_runner.run_source_recovery") as mock_run, \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["-h"])
            self.assertEqual(ctx.exception.code, 0)
        mock_get_db.assert_not_called()
        mock_run.assert_not_called()

    def test_unknown_option_exits_nonzero_no_side_effects(self):
        with patch("db.get_db") as mock_get_db, \
             patch("hackathon_source_recovery_runner.run_source_recovery") as mock_run, \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--bogus"])
            self.assertNotEqual(ctx.exception.code, 0)
        mock_get_db.assert_not_called()
        mock_run.assert_not_called()


class TestRunnerInvocation(unittest.TestCase):
    def test_no_arguments_still_persists(self):
        # Contract-preserving test: the live systemd timer invokes this
        # script with zero arguments and relies on it persisting.
        fake_conn = MagicMock()
        with patch("db.get_db", return_value=fake_conn) as mock_get_db, \
             patch("hackathon_source_recovery_runner.run_source_recovery",
                   return_value=FAKE_RESULT) as mock_run, \
             redirect_stdout(io.StringIO()):
            cli.main([])
        mock_get_db.assert_called_once()
        mock_run.assert_called_once_with(fake_conn, dry_run=False)
        fake_conn.close.assert_called_once()

    def test_dry_run_passes_dry_run_true(self):
        fake_conn = MagicMock()
        with patch("db.get_db", return_value=fake_conn) as mock_get_db, \
             patch("hackathon_source_recovery_runner.run_source_recovery",
                   return_value=FAKE_RESULT) as mock_run, \
             redirect_stdout(io.StringIO()):
            cli.main(["--dry-run"])
        mock_get_db.assert_called_once()
        mock_run.assert_called_once_with(fake_conn, dry_run=True)
        fake_conn.close.assert_called_once()

    def test_output_formatting_unchanged(self):
        fake_conn = MagicMock()
        result = {
            "events": [{"name": "Fake Hackathon", "result": "eligible"}],
            "summary": {
                "eligible": 1, "processed": 0, "candidates_found": 0,
                "candidates_inserted": 0, "duplicates_skipped": 0, "runner_errors": 0,
            },
        }
        with patch("db.get_db", return_value=fake_conn), \
             patch("hackathon_source_recovery_runner.run_source_recovery", return_value=result), \
             redirect_stdout(io.StringIO()) as out:
            cli.main(["--dry-run"])
        text = out.getvalue()
        self.assertIn("Event Radar — Source Recovery (dry run)", text)
        self.assertIn("Fake Hackathon", text)
        self.assertIn("Eligible: 1", text)


if __name__ == "__main__":
    unittest.main()
