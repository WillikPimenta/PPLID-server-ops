"""Testes de offsets compostos file/sqlite nos logs de deploy."""
from __future__ import annotations

import gc
import json
import sys
import tempfile
import unittest
from pathlib import Path

OPS_CONSOLE = Path(__file__).resolve().parent.parent
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import server_ops as so  # noqa: E402


class LogOffsetTests(unittest.TestCase):
    def test_parse_composite_offset(self) -> None:
        parsed = so.parse_log_offsets("build.log:f500/s12,promote.log:3")
        self.assertEqual(parsed["build.log"], {"file": 500, "sqlite": 12})
        self.assertEqual(parsed["promote.log"], {"file": 3, "sqlite": 0})

    def test_sqlite_fallback_when_file_caught_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env = "DEV"
            run_id = "offset-run"
            run_dir = base / "deploy" / env / "logs" / "runs" / run_id
            run_dir.mkdir(parents=True)
            log_path = run_dir / "build.log"
            log_path.write_text("[2026-01-01 00:00:00] [INFO] linha1\n", encoding="utf-8")

            ops_lib = Path(__file__).resolve().parents[4] / "ops" / "lib"
            if str(ops_lib) not in sys.path:
                sys.path.insert(0, str(ops_lib))
            import ops_store  # type: ignore

            db_path = base / "ops" / "data" / "ops-store.db"
            ops_store.init_store(db_path)
            ops_store.append_deploy_log(env, run_id, "build.log", "INFO", "sqlite-linha-2", db_path=db_path)
            ops_store.append_deploy_log(env, run_id, "build.log", "INFO", "sqlite-linha-3", db_path=db_path)
            (base / "machine.config.json").write_text(
                json.dumps({"baseDir": str(base), "opsStore": {"path": str(db_path)}}),
                encoding="utf-8",
            )

            chunk = so.load_run_log_chunk(
                base,
                env,
                run_id,
                "build.log",
                file_offset=1,
                sqlite_offset=0,
                limit=50,
            )
            self.assertGreaterEqual(len(chunk["lines"]), 2)
            joined = "\n".join(chunk["lines"])
            self.assertIn("sqlite-linha-2", joined)
            self.assertIn("sqlite-linha-3", joined)
            self.assertGreater(chunk.get("nextSqliteOffset", 0), 0)
            gc.collect()


if __name__ == "__main__":
    unittest.main()
