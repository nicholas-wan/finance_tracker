import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
serve = importlib.import_module('serve')
net_worth = importlib.import_module('net_worth')


class NetWorthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for key, path in [('NET_WORTH_PATH', root / 'manual' / 'net_worth.json'),
                          ('NET_WORTH_PUBLIC_PATH', root / 'app' / 'net_worth.json')]:
            patcher = patch.object(serve, key, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.account = dict(id='cpf_oa', name='CPF Ordinary Account', group='cpf', source='CPF portal')

    def test_account_then_snapshots_with_backup_and_stale_write_protection(self):
        first = serve.save_net_worth(dict(revision=0, account=self.account))
        self.assertEqual(first['revision'], 1)
        self.assertEqual(first['accounts'][0]['name'], 'CPF Ordinary Account')
        second = serve.save_net_worth(dict(revision=1, snapshot=dict(
            accountId='cpf_oa', date='2025-12-31', value=26406.83, source='Yearly statement')))
        self.assertEqual(second['snapshots'], [dict(accountId='cpf_oa', date='2025-12-31', value=26406.83, source='Yearly statement')])
        # Same account and date replaces rather than duplicates.
        third = serve.save_net_worth(dict(revision=2, snapshot=dict(accountId='cpf_oa', date='2025-12-31', value=26400)))
        self.assertEqual([s['value'] for s in third['snapshots']], [26400.0])
        self.assertEqual(json.loads(serve.NET_WORTH_PUBLIC_PATH.read_text()), serve.read_net_worth())
        backups = serve.backup_generations(serve.NET_WORTH_PATH)
        self.assertEqual(len(backups), 2)
        with self.assertRaisesRegex(ValueError, 'another tab'):
            serve.save_net_worth(dict(revision=1, account=self.account))
        self.assertEqual(serve.read_net_worth()['revision'], 3)

    def test_snapshots_sort_and_delete(self):
        serve.save_net_worth(dict(revision=0, account=self.account))
        serve.save_net_worth(dict(revision=1, snapshot=dict(accountId='cpf_oa', date='2026-09-06', value=38090.76)))
        state = serve.save_net_worth(dict(revision=2, snapshot=dict(accountId='cpf_oa', date='2024-12-31', value=9751.19)))
        self.assertEqual([s['date'] for s in state['snapshots']], ['2024-12-31', '2026-09-06'])
        state = serve.save_net_worth(dict(revision=3, deleteSnapshot=dict(accountId='cpf_oa', date='2024-12-31')))
        self.assertEqual([s['date'] for s in state['snapshots']], ['2026-09-06'])
        with self.assertRaisesRegex(ValueError, 'no longer recorded'):
            serve.save_net_worth(dict(revision=4, deleteSnapshot=dict(accountId='cpf_oa', date='2024-12-31')))

    def test_failed_publication_restores_previous_register(self):
        serve.save_net_worth(dict(revision=0, account=self.account))
        before = serve.NET_WORTH_PATH.read_bytes()
        original = serve.atomic_write_json

        def fail_public(path, payload):
            if path == serve.NET_WORTH_PUBLIC_PATH:
                raise OSError('test disk failure')
            return original(path, payload)
        with patch.object(serve, 'atomic_write_json', side_effect=fail_public):
            with self.assertRaises(OSError):
                serve.save_net_worth(dict(revision=1, snapshot=dict(accountId='cpf_oa', date='2026-01-01', value=1)))
        self.assertEqual(serve.NET_WORTH_PATH.read_bytes(), before)
        self.assertEqual(serve.NET_WORTH_PUBLIC_PATH.read_bytes(), before)

    def test_validation(self):
        current = dict(net_worth.EMPTY)
        for bad in [dict(id='x y', name='A', group='cash'), dict(id='a', name='', group='cash'),
                    dict(id='a', name='A', group='stocks'), dict(id='a', name='A', group='cash', extra='no'),
                    dict(id='a', name='A', group='cash', archived='yes')]:
            with self.assertRaises(ValueError):
                net_worth.apply_change(current, dict(account=bad))
        state = net_worth.apply_change(current, dict(account=dict(id='a', name='A', group='cash', archived=True)))
        self.assertTrue(state['accounts'][0]['archived'])
        for bad in [dict(accountId='zzz', date='2026-01-01', value=1), dict(accountId='a', date='2026-1-1', value=1),
                    dict(accountId='a', date='2026-01-01', value=-1), dict(accountId='a', date='2026-01-01', value=True),
                    dict(accountId='a', date='2026-01-01', value=float('nan')), dict(accountId='a', date='2026-01-01')]:
            with self.assertRaises(ValueError):
                net_worth.apply_change(state, dict(snapshot=bad))
        with self.assertRaisesRegex(ValueError, 'one account'):
            net_worth.apply_change(state, dict(account=dict(id='b', name='B', group='cash'), snapshot=dict(accountId='a', date='2026-01-01', value=1)))
        with self.assertRaises(ValueError):
            net_worth.apply_change(state, dict())
        with self.assertRaises(ValueError):
            serve.save_net_worth(dict(account=dict(id='a', name='A', group='cash')))

    def test_annual_top_up_schedule(self):
        state = net_worth.apply_change(dict(net_worth.EMPTY), dict(account=dict(
            id='srs', name='SRS', group='investments', topUp=dict(month=12, amount=15300, since=2025))))
        self.assertEqual(state['accounts'][0]['topUp'], dict(month=12, amount=15300.0, since=2025))
        state = net_worth.apply_change(state, dict(account=dict(
            id='srs', name='SRS', group='investments', topUp=dict(month=12, amount=15300, since=2025, flow='Retirement (SRS)'))))
        self.assertEqual(state['accounts'][0]['topUp']['flow'], 'Retirement (SRS)')
        with self.assertRaises(ValueError):
            net_worth.apply_change(state, dict(account=dict(id='srs', name='SRS', group='investments', topUp=dict(month=12, amount=1, since=2025, flow=''))))
        for bad in [dict(month=13, amount=1, since=2025), dict(month=12, amount=0, since=2025),
                    dict(month=12, amount=1, since=1999), dict(month=12, amount=1, since=2025, extra=1), 'yearly']:
            with self.assertRaises(ValueError):
                net_worth.apply_change(state, dict(account=dict(id='srs', name='SRS', group='investments', topUp=bad)))

    def test_reminders_are_carried_through_saves(self):
        reminder = dict(id='mum_cpf', name="Mum's CPF top-up", month=12, amount=2000, since=2025, match='CENTRAL PROVIDENT')
        current = dict(net_worth.EMPTY, reminders=[reminder])
        state = net_worth.apply_change(current, dict(account=dict(id='a', name='A', group='cash')))
        self.assertEqual(state['reminders'], [dict(reminder, amount=2000.0)])
        with self.assertRaises(ValueError):
            net_worth.apply_change(dict(current, reminders=[dict(reminder, month=0)]), dict(account=dict(id='a', name='A', group='cash')))
        with self.assertRaises(ValueError):
            net_worth.apply_change(dict(current, reminders=[dict(reminder, extra='x')]), dict(account=dict(id='a', name='A', group='cash')))
        self.assertNotIn('reminders', net_worth.apply_change(dict(net_worth.EMPTY), dict(account=dict(id='a', name='A', group='cash'))))

    def test_fresh_clone_is_seeded_and_published(self):
        self.assertFalse(serve.NET_WORTH_PATH.exists())
        self.assertEqual(serve.read_net_worth(), net_worth.EMPTY)
        with patch.object(serve, 'manual_file_defaults', return_value={serve.NET_WORTH_PATH: dict(net_worth.EMPTY)}):
            created = serve.ensure_manual_files()
        self.assertEqual(created, [serve.NET_WORTH_PATH])
        self.assertEqual(json.loads(serve.NET_WORTH_PATH.read_text()), net_worth.EMPTY)


if __name__ == '__main__':
    unittest.main()
