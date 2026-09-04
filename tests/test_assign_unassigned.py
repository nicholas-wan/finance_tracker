import contextlib
import importlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
assign_unassigned = importlib.import_module("assign_unassigned")


class AssignmentPolicyTests(unittest.TestCase):
    def test_shared_merchants_and_nic_remainder(self):
        rows = [
            {"id": "tx_1", "owner": "Untagged", "description": "Deliveroo Singapore"},
            {"id": "tx_2", "owner": "Untagged", "description": "Grab* Ride"},
            {"id": "tx_3", "owner": "Untagged", "description": "Other merchant"},
            {"id": "tx_4", "owner": "Yx", "description": "Grab* Already assigned"},
        ]
        self.assertEqual(
            assign_unassigned.plan_assignments(rows),
            {"tx_1": "Shared", "tx_2": "Shared", "tx_3": "Nic"},
        )


class AuditEntryTests(unittest.TestCase):
    ROWS = [
        {"id": "tx_1", "owner": "Untagged", "description": "Deliveroo",
         "date": "2026-06-01", "amount": 12.5},
        {"id": "tx_2", "owner": "Untagged", "description": "Shop", "amount": 3.0},
    ]

    def test_owner_change_lists_the_owners_the_batch_moved_to(self):
        entry = assign_unassigned.build_audit_entry(
            self.ROWS, {"tx_1": "Shared", "tx_2": "Nic"}, [])
        self.assertEqual(entry["transactionIds"], ["tx_1", "tx_2"])
        self.assertEqual(entry["action"], "Bulk-assigned owners for 2 transactions")
        self.assertEqual(entry["changes"], [
            {"field": "Owner", "before": "Untagged", "after": "Nic, Shared"},
        ])

    def test_pruned_stale_tags_add_a_second_change(self):
        entry = assign_unassigned.build_audit_entry(
            self.ROWS, {"tx_1": "Shared"}, ["tx_old_a", "tx_old_b"])
        self.assertEqual(entry["changes"][1], {
            "field": "Pruned stale tags", "before": "2", "after": "0",
        })

    def test_nothing_changed_makes_no_entry(self):
        self.assertIsNone(assign_unassigned.build_audit_entry(self.ROWS, {}, []))


class ServerGuardTests(unittest.TestCase):
    def test_answering_server_counts_as_running(self):
        with patch.object(assign_unassigned.urllib.request, "urlopen"):
            self.assertTrue(assign_unassigned.server_is_running(3402))

    def test_http_error_still_counts_as_running(self):
        error = urllib.error.HTTPError("u", 500, "boom", None, None)
        with patch.object(assign_unassigned.urllib.request, "urlopen",
                          side_effect=error):
            self.assertTrue(assign_unassigned.server_is_running(3402))

    def test_refused_connection_is_not_running(self):
        with patch.object(assign_unassigned.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("refused")):
            self.assertFalse(assign_unassigned.server_is_running(3402))

    def test_reachable_server_aborts_before_reading_anything(self):
        stderr = io.StringIO()
        with patch.object(assign_unassigned.urllib.request, "urlopen"), \
                patch.object(assign_unassigned, "load",
                             side_effect=AssertionError("must not read data")), \
                contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                assign_unassigned.main([])
        self.assertEqual(caught.exception.code, 1)
        self.assertIn(
            "The dashboard server is running; stop it first or tag from the "
            "dashboard.", stderr.getvalue())

    def test_force_runs_past_a_reachable_server(self):
        with Workspace(self) as workspace:
            with patch.object(assign_unassigned.urllib.request, "urlopen"), \
                    workspace.patched(), contextlib.redirect_stdout(io.StringIO()):
                assign_unassigned.main(["--force"])

    def test_custom_port_is_probed(self):
        with patch.object(assign_unassigned.urllib.request, "urlopen") as urlopen:
            urlopen.side_effect = urllib.error.URLError("refused")
            with Workspace(self) as workspace:
                with workspace.patched(), contextlib.redirect_stdout(io.StringIO()):
                    assign_unassigned.main(["--port", "9999"])
        self.assertIn("127.0.0.1:9999", urlopen.call_args[0][0])


class Workspace:
    """A throwaway manual/ + app/data/ tree the script can be pointed at."""

    def __init__(self, test, rows=None, tags=None, entries=None):
        self.test = test
        self.rows = rows if rows is not None else [
            {"id": "tx_1", "owner": "Untagged", "description": "Deliveroo Singapore",
             "date": "2026-06-01", "amount": 12.5},
            {"id": "tx_2", "owner": "Untagged", "description": "Corner Shop",
             "date": "2026-06-02", "amount": 4.0},
        ]
        self.tags = tags if tags is not None else {"tx_gone": "Nic"}
        self.entries = entries if entries is not None else [{"id": "audit_seed"}]
        self.calls = []
        self.build_fails = False
        self.validate_fails = False

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "manual").mkdir()
        (root / "app" / "data").mkdir(parents=True)
        self.owner_path = root / "manual" / "owner_tags.json"
        self.audit_path = root / "manual" / "audit_history.json"
        self.transactions_path = root / "app" / "data" / "transactions.json"
        self._dump(self.owner_path, {"tagsById": dict(self.tags)})
        self._dump(self.audit_path, {"entries": list(self.entries)})
        self._dump(self.transactions_path, {"transactions": self.rows})
        self.owner_before = self.owner_path.read_bytes()
        self.audit_before = self.audit_path.read_bytes()
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False

    @staticmethod
    def _dump(path, payload):
        path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def fake_run_script(self, script):
        """Stand in for build_data.py / validate_data.py.

        The build applies the saved stable-ID tags to the published rows, which
        is what main() re-reads to decide the batch really landed.
        """
        self.calls.append(script.name)
        if script.name == "build_data.py":
            if self.build_fails:
                raise RuntimeError("build failed")
            tags = self.read(self.owner_path).get("tagsById", {})
            rows = [dict(row) for row in self.rows]
            for row in rows:
                if row["id"] in tags:
                    row["owner"] = tags[row["id"]]
            self._dump(self.transactions_path, {"transactions": rows})
        elif script.name == "validate_data.py" and self.validate_fails:
            raise RuntimeError("validation failed")
        return ""

    def patched(self):
        return _patch_all(self)

    def backups(self):
        return sorted(p.name for p in self.owner_path.parent.glob("*.pre_bulk_*.bak"))


@contextlib.contextmanager
def _patch_all(workspace):
    with patch.object(assign_unassigned, "OWNER_PATH", workspace.owner_path), \
            patch.object(assign_unassigned, "AUDIT_PATH", workspace.audit_path), \
            patch.object(assign_unassigned, "TRANSACTIONS_PATH",
                         workspace.transactions_path), \
            patch.object(assign_unassigned, "run_script", workspace.fake_run_script):
        yield


class ApplyTests(unittest.TestCase):
    def run_apply(self, workspace, argv=("--apply",)):
        with patch.object(assign_unassigned.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("refused")), \
                workspace.patched(), contextlib.redirect_stdout(io.StringIO()):
            assign_unassigned.main(list(argv))

    def test_apply_writes_exactly_one_audit_entry_with_every_changed_id(self):
        with Workspace(self) as workspace:
            self.run_apply(workspace)
            entries = workspace.read(workspace.audit_path)["entries"]
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0], {"id": "audit_seed"})
            entry = entries[1]
            self.assertEqual(entry["transactionIds"], ["tx_1", "tx_2"])
            self.assertEqual(entry["action"],
                             "Bulk-assigned owners for 2 transactions")
            self.assertEqual(entry["changes"], [
                {"field": "Owner", "before": "Untagged", "after": "Nic, Shared"},
                {"field": "Pruned stale tags", "before": "1", "after": "0"},
            ])
            self.assertEqual(
                workspace.read(workspace.owner_path)["tagsById"],
                {"tx_1": "Shared", "tx_2": "Nic"},
            )

    def test_apply_backs_up_both_files_under_one_timestamp(self):
        with Workspace(self) as workspace:
            self.run_apply(workspace)
            names = workspace.backups()
            self.assertEqual(len(names), 2)
            stamps = {name.split(".pre_bulk_")[1] for name in names}
            self.assertEqual(len(stamps), 1)
            self.assertEqual(
                {name.split(".pre_bulk_")[0] for name in names},
                {"owner_tags.json", "audit_history.json"},
            )

    def test_preview_writes_nothing(self):
        with Workspace(self) as workspace:
            self.run_apply(workspace, argv=())
            self.assertEqual(workspace.owner_path.read_bytes(), workspace.owner_before)
            self.assertEqual(workspace.audit_path.read_bytes(), workspace.audit_before)
            self.assertEqual(workspace.calls, [])

    def test_validation_failure_restores_both_files(self):
        with Workspace(self) as workspace:
            workspace.validate_fails = True
            with self.assertRaises(RuntimeError):
                self.run_apply(workspace)
            self.assertEqual(workspace.owner_path.read_bytes(), workspace.owner_before)
            self.assertEqual(
                workspace.read(workspace.audit_path)["entries"],
                [{"id": "audit_seed"}],
            )
            # The rollback rebuilds so the dashboard matches the restored tags.
            self.assertEqual(workspace.calls[-1], "build_data.py")

    def test_rows_still_untagged_after_rebuild_restores_both_files(self):
        with Workspace(self) as workspace:
            # A build that publishes nothing leaves the rows Untagged, which the
            # script treats as a failed apply.
            workspace.fake_run_script = lambda script: workspace.calls.append(
                script.name)
            with self.assertRaises(RuntimeError):
                self.run_apply(workspace)
            self.assertEqual(workspace.owner_path.read_bytes(), workspace.owner_before)
            self.assertEqual(workspace.audit_path.read_bytes(), workspace.audit_before)


if __name__ == "__main__":
    unittest.main()
