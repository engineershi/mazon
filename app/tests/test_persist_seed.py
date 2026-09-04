# -*- coding: utf-8 -*-
"""Hermetic tests for first-boot DB seeding onto a persistent disk.

Runs `server` in a fresh subprocess so module-level state (DB, _RUNTIME keys,
rate limiters) can't leak into the rest of the suite, and so `import server`
exercises the real import-time `_ensure_db_file()` path.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args, env):
    proc = subprocess.run(
        [sys.executable, "-c", args],
        cwd=APP,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc


class TestSeedOnFirstBoot(unittest.TestCase):
    def test_missing_db_is_seeded_from_image_copy(self):
        tmp = tempfile.mkdtemp(prefix="pstore_seed_")
        db = os.path.join(tmp, "data", "pstore.db")
        env = dict(
            os.environ,
            PSTORE_DB=db,
            PSTORE_ADMIN_EMAIL="x@test.example",
            PSTORE_ADMIN_PASSWORD="pw",
        )
        code = (
            "import os, sqlite3, server;"
            "print('EXISTS', os.path.exists(server.DB));"
            "print('NICHES', sqlite3.connect(server.DB).execute("
            "'select count(*) from niches').fetchone()[0])"
        )
        proc = _run(code, env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("EXISTS True", proc.stdout)
        self.assertIn("NICHES 43", proc.stdout)

    def test_existing_db_is_not_overwritten(self):
        tmp = tempfile.mkdtemp(prefix="pstore_seed_")
        db = os.path.join(tmp, "pstore.db")
        marker = b"PRE-EXISTING-CONTENT"
        with open(db, "wb") as fh:
            fh.write(marker)
        env = dict(
            os.environ,
            PSTORE_DB=db,
            PSTORE_ADMIN_EMAIL="x@test.example",
            PSTORE_ADMIN_PASSWORD="pw",
        )
        code = (
            "import server;"
            "print(open(server.DB, 'rb').read())"
        )
        proc = _run(code, env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(marker.decode(), proc.stdout)


if __name__ == "__main__":
    unittest.main()