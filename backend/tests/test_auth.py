"""认证内核：口令校验与会话登记。

这一层不碰 HTTP、不读环境变量，所以过期可以靠"拨表"测，不用 sleep。
"""

import unittest

from app.auth import AdminAuth, AuthError, Session


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Login(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.auth = AdminAuth("correct horse battery", ttl_seconds=60, clock=self.clock)

    def test_correct_password_issues_a_session(self):
        session = self.auth.login("correct horse battery")

        self.assertIsInstance(session, Session)
        self.assertGreater(len(session.sid), 20)
        self.assertEqual(session.expires_at, 1060.0)
        self.assertEqual(self.auth.active_sessions(), 1)

    def test_each_login_gets_its_own_session(self):
        first = self.auth.login("correct horse battery")
        second = self.auth.login("correct horse battery")

        self.assertNotEqual(first.sid, second.sid)
        self.assertEqual(self.auth.active_sessions(), 2)

    def test_wrong_password_is_rejected_without_saying_why(self):
        with self.assertRaises(AuthError) as ctx:
            self.auth.login("wrong")

        self.assertEqual(ctx.exception.code, "INVALID_PASSWORD")
        self.assertEqual(self.auth.active_sessions(), 0)

    def test_empty_and_none_passwords_are_rejected(self):
        for attempt in ("", None):
            with self.assertRaises(AuthError) as ctx:
                self.auth.login(attempt)
            self.assertEqual(ctx.exception.code, "INVALID_PASSWORD")

    def test_empty_configured_password_refuses_to_start(self):
        with self.assertRaises(ValueError) as ctx:
            AdminAuth("")
        self.assertIn("ADMIN_PASSWORD", str(ctx.exception))


class Verify(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.auth = AdminAuth("pw", ttl_seconds=60, clock=self.clock)

    def test_missing_cookie_is_not_a_session(self):
        with self.assertRaises(AuthError) as ctx:
            self.auth.verify(None)
        self.assertEqual(ctx.exception.code, "NO_SESSION")

    def test_forged_cookie_is_not_a_session(self):
        with self.assertRaises(AuthError) as ctx:
            self.auth.verify("made-up-sid")
        self.assertEqual(ctx.exception.code, "NO_SESSION")

    def test_issued_session_can_be_verified(self):
        session = self.auth.login("pw")
        self.assertEqual(self.auth.verify(session.sid).sid, session.sid)

    def test_session_expires_after_ttl(self):
        session = self.auth.login("pw")

        self.clock.now += 60.0

        with self.assertRaises(AuthError) as ctx:
            self.auth.verify(session.sid)
        self.assertEqual(ctx.exception.code, "EXPIRED")

    def test_expired_session_is_dropped_from_the_registry(self):
        session = self.auth.login("pw")
        self.clock.now += 61.0

        with self.assertRaises(AuthError):
            self.auth.verify(session.sid)

        self.assertEqual(self.auth.active_sessions(), 0)


class Logout(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.auth = AdminAuth("pw", ttl_seconds=60, clock=self.clock)

    def test_logout_makes_the_old_cookie_useless_immediately(self):
        session = self.auth.login("pw")
        self.auth.verify(session.sid)          # 退出前可用

        self.auth.logout(session.sid)

        with self.assertRaises(AuthError) as ctx:
            self.auth.verify(session.sid)
        self.assertEqual(ctx.exception.code, "NO_SESSION")

    def test_logout_is_idempotent(self):
        session = self.auth.login("pw")
        self.assertTrue(self.auth.logout(session.sid))
        self.assertFalse(self.auth.logout(session.sid))
        self.assertFalse(self.auth.logout(None))
        self.assertFalse(self.auth.logout("never-issued"))

    def test_logging_out_one_session_leaves_the_other_alone(self):
        first = self.auth.login("pw")
        second = self.auth.login("pw")

        self.auth.logout(first.sid)

        self.assertEqual(self.auth.verify(second.sid).sid, second.sid)


if __name__ == "__main__":
    unittest.main()
