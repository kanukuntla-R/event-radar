"""Tests for the run_az_tech_council_discovery.py CLI's safety behavior:
--help/-h and invalid invocations must never touch the network or the
database, no-argument invocation must not persist, and --dry-run/--persist
must map onto the runner's dry_run flag correctly.
"""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import run_az_tech_council_discovery as cli

FAKE_RESULT = {
    "events": [],
    "summary": {
        "pages_fetched": 0, "candidates_found": 0, "included": 0, "review": 0,
        "excluded": 0, "inserted": 0, "duplicates_skipped": 0, "review_stored": 0,
        "review_existing": 0, "invalid_skipped": 0, "runner_errors": 0,
    },
}


class TestArgParsing(unittest.TestCase):
    def test_help_exits_zero_no_side_effects(self):
        with patch("db.get_db") as mock_get_db, \
             patch("az_tech_council_discovery_runner.run_az_tech_council_discovery") as mock_run, \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--help"])
            self.assertEqual(ctx.exception.code, 0)
        mock_get_db.assert_not_called()
        mock_run.assert_not_called()

    def test_short_help_flag_behaves_the_same(self):
        with patch("db.get_db") as mock_get_db, \
             patch("az_tech_council_discovery_runner.run_az_tech_council_discovery") as mock_run, \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["-h"])
            self.assertEqual(ctx.exception.code, 0)
        mock_get_db.assert_not_called()
        mock_run.assert_not_called()

    def test_unknown_option_exits_nonzero_no_side_effects(self):
        with patch("db.get_db") as mock_get_db, \
             patch("az_tech_council_discovery_runner.run_az_tech_council_discovery") as mock_run, \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--bogus"])
            self.assertNotEqual(ctx.exception.code, 0)
        mock_get_db.assert_not_called()
        mock_run.assert_not_called()

    def test_no_arguments_cannot_persist(self):
        with patch("db.get_db") as mock_get_db, \
             patch("az_tech_council_discovery_runner.run_az_tech_council_discovery") as mock_run, \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main([])
            self.assertNotEqual(ctx.exception.code, 0)
        mock_get_db.assert_not_called()
        mock_run.assert_not_called()

    def test_dry_run_and_persist_together_rejected(self):
        with patch("db.get_db") as mock_get_db, \
             patch("az_tech_council_discovery_runner.run_az_tech_council_discovery") as mock_run, \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--dry-run", "--persist"])
            self.assertNotEqual(ctx.exception.code, 0)
        mock_get_db.assert_not_called()
        mock_run.assert_not_called()


class TestRunnerInvocation(unittest.TestCase):
    def test_dry_run_passes_dry_run_true(self):
        fake_conn = MagicMock()
        with patch("db.get_db", return_value=fake_conn) as mock_get_db, \
             patch("az_tech_council_discovery_runner.run_az_tech_council_discovery",
                   return_value=FAKE_RESULT) as mock_run, \
             redirect_stdout(io.StringIO()):
            cli.main(["--dry-run"])
        mock_get_db.assert_called_once()
        mock_run.assert_called_once_with(fake_conn, dry_run=True)
        fake_conn.close.assert_called_once()

    def test_persist_passes_dry_run_false(self):
        fake_conn = MagicMock()
        with patch("db.get_db", return_value=fake_conn) as mock_get_db, \
             patch("az_tech_council_discovery_runner.run_az_tech_council_discovery",
                   return_value=FAKE_RESULT) as mock_run, \
             redirect_stdout(io.StringIO()):
            cli.main(["--persist"])
        mock_get_db.assert_called_once()
        mock_run.assert_called_once_with(fake_conn, dry_run=False)
        fake_conn.close.assert_called_once()

    def test_output_formatting_unchanged(self):
        fake_conn = MagicMock()
        result = {
            "events": [
                {"name": "Fake Event", "result": "new", "event_id": 42},
            ],
            "summary": {
                "pages_fetched": 1, "candidates_found": 1, "included": 1, "review": 0,
                "excluded": 0, "inserted": 1, "duplicates_skipped": 0, "review_stored": 0,
                "review_existing": 0, "invalid_skipped": 0, "runner_errors": 0,
            },
        }
        with patch("db.get_db", return_value=fake_conn), \
             patch("az_tech_council_discovery_runner.run_az_tech_council_discovery", return_value=result), \
             redirect_stdout(io.StringIO()) as out:
            cli.main(["--dry-run"])
        text = out.getvalue()
        self.assertIn("Event Radar — Arizona Technology Council Discovery (dry run)", text)
        self.assertIn("Fake Event (event id 42)", text)
        self.assertIn("Inserted: 1", text)


if __name__ == "__main__":
    unittest.main()
