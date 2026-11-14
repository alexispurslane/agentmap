"""Behavior of the command line."""

import http.client
import io
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from agentmap.__main__ import DEFAULT_DB, main, parse_args


class MainTest(unittest.TestCase):
    def test_defaults_watch_three_hours_on_port_7788_with_the_t3_database(self):
        args = parse_args(["--project", "/tmp"])
        self.assertEqual((args.port, args.hours, args.t3_db), (7788, 3.0, DEFAULT_DB))
        self.assertEqual(DEFAULT_DB, Path.home() / ".t3" / "userdata" / "statev2.sqlite")

    def test_a_missing_project_directory_stops_with_status_2(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(["--project", "/no/such/dir"]), 2)
        self.assertIn("is not a directory", err.getvalue())

    def test_a_missing_database_stops_with_status_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(["--project", tmp, "--t3-db", f"{tmp}/none.sqlite"]), 2)
            self.assertIn("database not found", err.getvalue())


class EndToEndTest(unittest.TestCase):
    def test_the_program_serves_the_page_and_stops_cleanly_on_interrupt(self):
        schema = (Path(__file__).parent / "t3_fixture_schema.sql").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            with sqlite3.connect(db) as conn:
                conn.executescript(schema)
            project = Path(tmp) / "proj"
            (project / "src").mkdir(parents=True)
            (project / "src" / "a.py").write_text("x\n")
            root = Path(__file__).parent.parent
            proc = subprocess.Popen(
                [sys.executable, "-m", "agentmap", "--project", str(project), "--port", "0",
                 "--t3-db", str(db)],
                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                lines = [proc.stdout.readline() for _ in range(3)]
                self.assertIn("watching", lines[0])
                port = int(lines[1].rsplit(":", 1)[1].strip("/\n"))
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/")
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
                proc.send_signal(signal.SIGINT)
                self.assertEqual(proc.wait(10), 0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.stdout.close()
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
