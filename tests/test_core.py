"""Retained CLI contracts: identity checks, private storage, run results and cancellation."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from filelock import FileLock, Timeout

from godoon import auth, runner
from godoon.api import AuthenticationError, CLUB_ID
from godoon.geometry import gcj02
from godoon.storage import Store

ROUTE = Path(__file__).resolve().parents[1] / 'route.json'


def test_token(expires=4102444800):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return '.'.join((encode({'alg': 'HS256', 'typ': 'JWT'}), encode({'exp': expires}), 'x' * 43))


class VirtualClock:
    def __init__(self, cancel_at=None):
        self.now = 0
        self.cancel_at = cancel_at

    def is_set(self):
        return self.cancel_at is not None and self.now >= self.cancel_at

    def wait(self, seconds):
        self.now += max(0, seconds)
        return self.is_set()


class FakeBackend:
    """In-memory record lifecycle, keeping tests independent of real accounts and API availability."""

    user_id = 'test-member'

    def __init__(self, already_done=False, interfere=False):
        self.active = ''
        self.credit = int(already_done)
        self.count = 7
        self.points = []
        self.record = None
        self.created = 0
        self.deleted = []
        self.interfere = interfere

    def request(self, method, path, data):
        if path.endswith('/48472/detail'):
            return {'club_challenge_id': 1, 'total_steps': self.count, 'total_steps_limit': 24}
        if path.endswith('/challenge_member_daily_score'):
            day = runner.datetime.fromtimestamp(data['cur_day'], runner.ZONE).date().isoformat()
            return {'cur_day': day, 'run_score_list': [{'score': self.credit}] if self.credit else []}
        if path.endswith('/challenge_detail'):
            return {'score_rule': [{'score_type': 3, 'state': 1, 'base_value': 3000}]}
        if path.endswith('/get_route_config'):
            if self.interfere and len(self.points) > 5:
                self.active = 'another-run'
            return {'route_id': self.active}
        if path.endswith('/start_route'):
            self.created += 1
            self.active = 'owned-run'
            return {'route_id': self.active}
        if path.endswith('/create_route_point'):
            self.points.extend(data['points'])
            return {}
        if path.endswith('/create_route_gyroscope_steps'):
            return {}
        if path.endswith('/completes_route'):
            if data['is_delete']:
                self.deleted.append(data['route_id'])
            else:
                last = self.points[-1]
                self.record = {'route_id': self.active, 'total_length': int(last['to_start_distance']),
                               'total_time': int(last['to_start_dost_time'] / 1000), 'pace': 422,
                               'is_fraud': False, 'time_duplicate': False}
                self.count += 1
                self.credit = 1
            self.active = ''
            return {}
        if path.endswith('/get_user_gps_info/list'):
            return {'list': [self.record] if self.record else []}
        raise AssertionError(f'Unexpected endpoint: {path}')


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / 'state')

    def execute(self, backend, clock):
        with patch('godoon.runner.time.monotonic', side_effect=lambda: clock.now), \
                patch('godoon.runner.time.time', side_effect=lambda: 2000000000 + clock.now), \
                patch('builtins.print'):
            runner.run(backend, self.store, ROUTE, clock)

    def test_complete_run_requires_saved_record_and_credit(self):
        backend, clock = FakeBackend(), VirtualClock()
        self.execute(backend, clock)
        saved = self.store.load('run.json')
        self.assertTrue(saved['verified'])
        self.assertEqual(saved['after_count'], 8)
        self.assertGreaterEqual(backend.record['total_length'], 3100)
        self.assertGreaterEqual(backend.record['total_time'], 1300)
        self.assertEqual(backend.deleted, [])
        self.assertEqual(backend.active, '')

    def test_actual_daily_credit_prevents_duplicate_even_without_template_flag(self):
        backend = FakeBackend(already_done=True)
        self.execute(backend, VirtualClock())
        self.assertEqual(backend.created, 0)

    def test_cancel_deletes_only_own_unfinished_record(self):
        backend = FakeBackend()
        with self.assertRaises(InterruptedError):
            self.execute(backend, VirtualClock(cancel_at=30))
        self.assertEqual(backend.deleted, ['owned-run'])
        self.assertTrue(self.store.load('run.json')['partial_record_deleted'])

    def test_foreign_active_record_is_never_ended(self):
        backend = FakeBackend(interfere=True)
        with self.assertRaisesRegex(RuntimeError, '会话发生变化'):
            self.execute(backend, VirtualClock())
        self.assertEqual(backend.deleted, [])
        self.assertEqual(backend.active, 'another-run')

    def test_short_flagged_or_overlapping_record_is_rejected(self):
        for length, fraud, duplicate in ((716, False, False), (3100, True, False), (3100, False, True)):
            with self.subTest(length=length, fraud=fraud, duplicate=duplicate), self.assertRaises(RuntimeError):
                runner.validate_record({'total_length': length, 'is_fraud': fraud, 'time_duplicate': duplicate}, 3000)

    def test_coordinate_frame_matches_known_conversion(self):
        latitude, longitude = gcj02(39.915, 116.404)
        self.assertAlmostEqual(latitude, 39.91640428150164, places=8)
        self.assertAlmostEqual(longitude, 116.41024449916938, places=8)

    def test_invalid_route_fails_before_execution(self):
        route = json.loads(ROUTE.read_text(encoding='utf-8'))
        for value in (0, -1, float('nan')):
            route['speed_mps'] = value
            path = self.store.directory / 'route.json'
            path.write_text(json.dumps(route), encoding='utf-8')
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                runner.load_route(path)

    def test_private_state_and_single_process_lock(self):
        self.store.save('session.json', {'access_token': 'test-only'})
        self.assertEqual(self.store.load('session.json')['access_token'], 'test-only')
        if os.name != 'nt':
            self.assertEqual((self.store.directory / 'session.json').stat().st_mode & 0o777, 0o600)
        with self.store.lock:
            with self.assertRaises(Timeout):
                FileLock(self.store.directory / 'run.lock', timeout=0).acquire()

    def test_login_bootstraps_without_any_existing_account_file(self):
        token = test_token()
        with patch('godoon.auth.getpass.getpass', return_value=token), patch('godoon.auth.Client') as factory, patch('builtins.print'):
            factory.return_value.request.side_effect = [
                {'user_id': 'test-member', 'last_club': {'id': CLUB_ID}},
                {'total_steps': 0, 'total_steps_limit': 24},
            ]
            auth.login(self.store, manual=True)
        self.assertEqual(self.store.load('session.json')['user_id'], 'test-member')

    def test_login_rejects_expired_and_wrong_school_credentials(self):
        with self.assertRaises(AuthenticationError):
            auth.verify(test_token(expires=1))
        with patch('godoon.auth.Client') as factory:
            factory.return_value.request.return_value = {'user_id': 'test-member', 'last_club': {'id': 123}}
            with self.assertRaises(AuthenticationError):
                auth.verify(test_token())
        self.assertIsNone(self.store.load('session.json'))


if __name__ == '__main__':
    unittest.main()
