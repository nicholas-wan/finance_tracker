import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import_all = importlib.import_module("import_all")


class ImportAllTests(unittest.TestCase):
    def fixture_run(self, fail_validation=False):
        def run(script, staging, strict=False):
            if script == "validate_data.py":
                if fail_validation:
                    raise RuntimeError("synthetic validation failure")
                return
            names = {
                "parse_cc.py": "card_transactions.json",
                "parse_one.py": "account_transactions.json",
                "build_data.py": "transactions.json",
            }
            (staging / names[script]).write_text(
                json.dumps({"source": script}), encoding="utf-8"
            )

        return run

    def invoke(self, root, run):
        live = root / "app" / "data"
        lock = root / "tmp" / ".finance-data.lock"
        with patch.object(import_all, "REPO_ROOT", root), \
                patch.object(import_all, "LIVE_DATA_DIR", live), \
                patch.object(import_all, "LOCK_PATH", lock), \
                patch.object(import_all, "run", side_effect=run):
            return import_all.main([])

    def test_validation_failure_does_not_replace_any_live_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "app" / "data"
            live.mkdir(parents=True)
            originals = {}
            for name in import_all.GENERATED_FILES:
                content = ("old-" + name).encode()
                (live / name).write_bytes(content)
                originals[name] = content

            with self.assertRaisesRegex(RuntimeError, "validation failure"):
                self.invoke(root, self.fixture_run(fail_validation=True))

            self.assertEqual(
                {name: (live / name).read_bytes()
                 for name in import_all.GENERATED_FILES},
                originals,
            )

    def test_success_publishes_one_generation_to_all_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(self.invoke(root, self.fixture_run()), 0)
            payloads = [
                json.loads((root / "app" / "data" / name).read_text(encoding="utf-8"))
                for name in import_all.GENERATED_FILES
            ]
            generations = {payload["generationId"] for payload in payloads}
            self.assertEqual(len(generations), 1)
            self.assertTrue(next(iter(generations)).startswith("generation_"))


if __name__ == "__main__":
    unittest.main()
