import importlib
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
serve = importlib.import_module('serve')
validate_record = importlib.import_module('home_records').validate_record


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for key, path in [('HOME_PATH', root / 'manual' / 'home.json'),
                          ('HOME_PUBLIC_PATH', root / 'app' / 'home.json'),
                          ('TRANSACTIONS_PATH', root / 'card.json'),
                          ('ACCOUNT_TRANSACTIONS_PATH', root / 'bank.json')]:
            patcher = patch.object(serve, key, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.record = dict(id='fridge', kind='appliance', name='Fridge', status='Needs checking')

    def test_save_reload_backup_and_stale_write_protection(self):
        first = serve.save_home(dict(revision=0, record=self.record))
        self.assertEqual(first['revision'], 1)
        self.assertNotIn('cost', first['records'][0])
        changed = dict(self.record, cost=123.45)
        serve.save_home(dict(revision=1, record=changed))
        self.assertEqual(serve.read_home()['records'][0]['cost'], 123.45)
        self.assertEqual(json.loads(serve.HOME_PUBLIC_PATH.read_text()), serve.read_home())
        backups = serve.backup_generations(serve.HOME_PATH)
        self.assertEqual(len(backups), 1)
        self.assertNotIn('cost', json.loads(backups[0].read_text())['records'][0])
        with self.assertRaisesRegex(ValueError, 'another tab'):
            serve.save_home(dict(revision=1, record=self.record))
        self.assertEqual(serve.read_home()['revision'], 2)

    def test_failed_publication_restores_previous_register(self):
        serve.save_home(dict(revision=0, record=self.record))
        before = serve.HOME_PATH.read_bytes()
        original = serve.atomic_write_json
        def fail_public(path, payload):
            if path == serve.HOME_PUBLIC_PATH:
                raise OSError('test disk failure')
            return original(path, payload)
        with patch.object(serve, 'atomic_write_json', side_effect=fail_public):
            with self.assertRaises(OSError):
                serve.save_home(dict(revision=1, record=dict(self.record, cost=99)))
        self.assertEqual(serve.HOME_PATH.read_bytes(), before)
        self.assertEqual(serve.HOME_PUBLIC_PATH.read_bytes(), before)

    def test_record_edit_preserves_property_summary(self):
        property_data = {'name': 'Test flat', 'purchasePrice': 100, 'fees': [{'label': 'Fee', 'amount': 2}]}
        serve.HOME_PATH.parent.mkdir(parents=True, exist_ok=True)
        serve.HOME_PATH.write_text(json.dumps({'revision': 0, 'records': [], 'property': property_data}))
        result = serve.save_home(dict(revision=0, record=self.record))
        self.assertEqual(result['property'], property_data)
        self.assertEqual(json.loads(serve.HOME_PUBLIC_PATH.read_text())['property'], property_data)

    def test_validation_rejects_bad_values_and_unknown_payment(self):
        for changes in [dict(cost=-1), dict(cost=True), dict(cost=float('nan')),
                        dict(expires='2026-02-30'), dict(sourceUrl='javascript:alert(1)'),
                        dict(sourceUrl='https://user:password@example.com/'),
                        dict(transactionId='missing', transactionSource='card'),
                        dict(starts='2026-09-01', expires='2025-09-01')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_record(dict(self.record, **changes), set())

    def test_payment_links_validate_and_survive_save(self):
        serve.TRANSACTIONS_PATH.write_text(json.dumps({'transactions': [{'id': 'payment_a'}]}))
        link = {'source': 'card', 'id': 'payment_a'}
        record = dict(self.record, paymentLinks=[link])
        saved = serve.save_home(dict(revision=0, record=record))
        self.assertEqual(saved['records'][0]['paymentLinks'], [link])
        for links in [[link, link], [{'source': 'card', 'id': 'missing'}], [{'source': 'bank', 'id': 'payment_a'}]]:
            with self.assertRaises(ValueError):
                validate_record(dict(self.record, paymentLinks=links), {('card', 'payment_a')})

    def test_maintenance_schedule_is_validated(self):
        base = {'id': 'home_water_dispenser_filter_package', 'kind': 'maintenance', 'name': 'Filters',
                'status': 'Verified', 'linkedRecord': 'home_water_dispenser', 'installationType': 'Delivery',
                'installationSource': 'Owner',
                'schedule': [{'label': ' Year 1 of 3 ', 'due': '2026-08-29', 'done': False},
                             {'label': 'Year 2 of 3', 'due': '2027-08-29', 'done': True, 'completed': '2027-09-01'}]}
        saved = validate_record(base, set())
        self.assertEqual(saved['schedule'], [{'label': 'Year 1 of 3', 'due': '2026-08-29', 'done': False},
                                             {'label': 'Year 2 of 3', 'due': '2027-08-29', 'done': True, 'completed': '2027-09-01'}])
        self.assertEqual(saved['linkedRecord'], 'home_water_dispenser')
        for bad in ['not a list', [{'label': 'x'}], [{'label': 'x', 'due': '2026-13-01', 'done': False}],
                    [{'label': 'x', 'due': '2026-08-29', 'done': 'yes'}],
                    [{'label': 'x', 'due': '2026-08-29', 'done': False, 'extra': 1}],
                    [{'label': 'x', 'due': '2026-08-29', 'done': True, 'completed': 'soon'}]]:
            with self.assertRaises(ValueError):
                validate_record(dict(base, schedule=bad), set())
        with self.assertRaises(ValueError):
            validate_record(dict(base, linkedRecord='../etc'), set())

    def test_payment_reference_does_not_mutate_statement(self):
        serve.TRANSACTIONS_PATH.write_text(json.dumps({'transactions':[{'id':'tx1','amount':150}]}))
        before = serve.TRANSACTIONS_PATH.read_bytes()
        record = dict(self.record, transactionId='tx1', transactionSource='card', cost=150)
        serve.save_home(dict(revision=0, record=record))
        self.assertEqual(serve.TRANSACTIONS_PATH.read_bytes(), before)

    def test_item_save_preserves_house_sheet_reference(self):
        serve.HOME_PATH.parent.mkdir(parents=True)
        reference = {'groups': [{'name': 'Furniture', 'reportedTotal': 100}],
                     'sourceUrl': 'https://docs.google.com/spreadsheets/d/example/edit'}
        serve.atomic_write_json(serve.HOME_PATH, dict(revision=0, records=[], costReference=reference))
        record = dict(self.record, category='Appliances', warrantyTerms='General cover',
                      secondaryWarranty='Compressor', secondaryExpiry='2029-10-02',
                      costBasis='Receipt / sales order', funding='Housewarming gift')
        serve.save_home(dict(revision=0, record=record))
        self.assertEqual(serve.read_home()['costReference'], reference)
        self.assertEqual(serve.read_home()['records'][0]['secondaryExpiry'], '2029-10-02')
        with self.assertRaises(ValueError):
            validate_record(dict(record, category='Invented category'), set())

    def test_http_local_origin_and_share_read_only(self):
        local = serve.ThreadingHTTPServer(('127.0.0.1', 0), serve.FinanceHandler)
        shared = serve.ThreadingHTTPServer(('127.0.0.1', 0), serve.ShareHandler)
        shared.share_token = 'home-test-token'
        servers = [local, shared]
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        def request(server, method, path, payload=None, origin=None):
            port = server.server_address[1]
            connection = http.client.HTTPConnection('127.0.0.1', port)
            headers = {'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            connection.request(method, path, json.dumps(payload) if payload else None, headers)
            response = connection.getresponse()
            status, data = response.status, json.loads(response.read())
            connection.close()
            return status, data
        try:
            payload = dict(revision=0, record=self.record)
            self.assertEqual(request(local, 'POST', '/api/home', payload, 'https://example.com')[0], 403)
            self.assertEqual(request(local, 'POST', '/api/home', payload,
                                     'http://127.0.0.1:%d' % local.server_address[1])[0], 200)
            self.assertEqual(request(local, 'GET', '/api/home')[1]['revision'], 1)
            self.assertEqual(request(shared, 'GET', '/api/home')[0], 403)
            path = '/api/home?k=home-test-token'
            self.assertEqual(request(shared, 'GET', path)[1]['records'][0]['id'], 'fridge')
            self.assertEqual(request(shared, 'POST', path, payload)[0], 405)
            self.assertEqual(serve.read_home()['revision'], 1)
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
