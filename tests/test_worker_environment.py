import os
import tempfile
import unittest
from pathlib import Path

from helpdesk.worker_contracts import HealthState
from helpdesk.worker_environment import ProfileManager, classify_health, minimal_state
from tests.test_worker_contracts import command, health


def observation(**changes):
    value = dict(trusted_controls=True, connected=True, interactive_desktop=True,
        desktop_unlocked=True, login_control=False, verification_control=False,
        rate_limit_control=False, access_denied_control=False, expected_page_controls=True)
    value.update(changes)
    return value


class WorkerEnvironmentTests(unittest.TestCase):
    def test_classification_requires_trusted_controls_not_student_text(self):
        self.assertEqual(classify_health(observation()), HealthState.HEALTHY)
        for text in ('请登录', '验证码', '请求过于频繁'):
            self.assertEqual(classify_health({'student_text': text}), HealthState.UNKNOWN)
        self.assertEqual(classify_health(observation(trusted_controls=False)), HealthState.UNKNOWN)
        self.assertEqual(classify_health(observation(login_control=1)), HealthState.UNKNOWN)

    def test_all_health_conditions_are_distinct(self):
        for field, state in [('login_control','LOGIN_REQUIRED'),('verification_control','VERIFICATION_REQUIRED'),
                ('rate_limit_control','RATE_LIMITED'),('access_denied_control','ACCESS_DENIED'),
                ('desktop_unlocked','DESKTOP_UNAVAILABLE'),('expected_page_controls','PAGE_CHANGED')]:
            self.assertEqual(classify_health(observation(**{field: field.endswith('_control')})), state)

    def test_dedicated_profiles_reject_default_escape_and_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            p = ProfileManager(Path(directory) / 'dedicated')
            for path in (p.root / 'Default', p.root / 'User Data' / 'other', Path(directory) / 'normal', p.root):
                with self.assertRaises(ValueError): p.register('p-1','account-1',path)
            result = p.register('p-1','account-1',p.root/'browser-one')
            self.assertNotIn('path', result)
            self.assertEqual(p.register('p-1','account-1',p.root/'browser-one'),result)
            for args in [('p-2','account-2',p.root/'browser-one'),('p-2','account-1',p.root/'browser-two'),
                         ('p-1','account-1',p.root/'browser-two')]:
                with self.assertRaises(ValueError):p.register(*args)
            with p.acquire('p-1','account-1') as local:
                self.assertEqual(local,p.root/'browser-one')
                with self.assertRaises(TimeoutError):
                    with ProfileManager(p.root).acquire('p-1','account-1',timeout=.01):pass
            with self.assertRaises(ValueError):
                with p.acquire('p-1','account-2'):pass

    def test_log_allowlist_does_not_include_profile_or_source(self):
        result=minimal_state(health(command()))
        self.assertEqual(set(result),{'worker_id','account_id','state','observed_at','native_call_pending'})

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_registered_profile_cannot_be_redirected_to_another_account_by_junction(self):
        import _winapi
        with tempfile.TemporaryDirectory() as directory:
            p = ProfileManager(Path(directory) / 'dedicated')
            a, b = p.root / 'profile-a', p.root / 'profile-b'
            p.register('profile-a', 'account-a', a)
            p.register('profile-b', 'account-b', b)
            a.rmdir()  # Both directories are empty anonymous test fixtures.
            _winapi.CreateJunction(str(b), str(a))
            try:
                self.assertTrue(a.is_junction())
                with self.assertRaisesRegex(ValueError, 'PROFILE_PATH_CHANGED'):
                    with p.acquire('profile-a', 'account-a'):
                        self.fail('Account A must never acquire account B profile')
            finally:
                a.rmdir()  # Remove only the junction, never its target.
            self.assertTrue(b.is_dir())
            with p.acquire('profile-b', 'account-b') as approved:
                self.assertEqual(approved, b)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_registration_rejects_junction_alias_even_within_dedicated_root(self):
        import _winapi
        with tempfile.TemporaryDirectory() as directory:
            p = ProfileManager(Path(directory) / 'dedicated')
            target, link = p.root / 'anonymous-target', p.root / 'alias'
            target.mkdir()
            _winapi.CreateJunction(str(target), str(link))
            try:
                with self.assertRaisesRegex(ValueError, 'PROFILE_PATH_CHANGED'):
                    p.register('profile-a', 'account-a', link)
                with self.assertRaisesRegex(ValueError, 'PROFILE_PATH_CHANGED'):
                    ProfileManager(link / 'nested-root')
            finally:
                link.rmdir()
            self.assertTrue(target.is_dir())


if __name__ == '__main__': unittest.main()
