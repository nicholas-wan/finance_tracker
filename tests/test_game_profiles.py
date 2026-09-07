import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import serve

class GameProfileTests(unittest.TestCase):
    def payload(self, **profile):
        return {'title': 'Example game', 'revision': 0, 'profile': dict(note='Enjoying it', **profile)}

    def test_validation(self):
        self.assertEqual(serve.validate_game_profile(self.payload())[1]['note'], 'Enjoying it')
        for field, value in [('hours', 1), ('status', 'Playing'), ('note', 'x'*501)]:
            data=self.payload();data['profile'][field]=value
            with self.assertRaises(ValueError):serve.validate_game_profile(data)

    def test_save_conflict_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profiles.json'
            def rebuilt(_):return {'gameProfiles': json.loads(path.read_text())}
            with patch.object(serve, 'GAME_PROFILES_PATH', path), patch.object(serve, 'run_script', return_value='ok'), patch.object(serve, 'load_json', side_effect=rebuilt), patch.object(serve, 'snapshot_backups'):
                result=serve.save_game_profile(self.payload())
                self.assertEqual(result['gameProfiles']['records']['Example game']['note'], 'Enjoying it')
                original=path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'another window'):serve.save_game_profile(self.payload())
                self.assertEqual(path.read_bytes(),original)
                data=self.payload();data['revision']=1;data['profile']['note']='Updated note'
                with patch.object(serve, 'load_json', return_value={}):
                    with self.assertRaisesRegex(RuntimeError, 'did not apply'):serve.save_game_profile(data)
                self.assertEqual(path.read_bytes(),original)
