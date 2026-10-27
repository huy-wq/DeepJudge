"""HTTP 层的验收：全是"访问某个地址得到什么"。

接缝就是 HTTP——验收标准写的是状态码与响应，所以用真请求测，
不去断言内部函数被调了几次。
"""

import unittest
from unittest import mock

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("未安装 fastapi/httpx，见 backend/pyproject.toml")

from app.auth import SESSION_COOKIE, AdminAuth
from app.main import create_app

PASSWORD = "let-me-in-please"


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.auth = AdminAuth(PASSWORD, ttl_seconds=60, clock=self.clock)
        self.app = create_app(auth=self.auth)
        self.client = TestClient(self.app)

    def login(self, password: str = PASSWORD):
        return self.client.post("/api/auth/login", json={"password": password})

    def assert_unauthenticated(self, response):
        self.assertEqual(response.status_code, 401, response.text)
        self.assertNotIn("password", response.text.lower())


class ProtectedRoute(ApiTestCase):
    def test_without_credential_returns_401(self):
        response = self.client.get("/api/auth/session")

        self.assert_unauthenticated(response)
        self.assertEqual(response.json()["detail"], "未登录或会话已失效")

    def test_with_forged_cookie_returns_401(self):
        self.client.cookies.set(SESSION_COOKIE, "not-a-real-session")

        self.assert_unauthenticated(self.client.get("/api/auth/session"))

    def test_public_health_check_still_works_without_login(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class Login(ApiTestCase):
    def test_correct_password_issues_a_cookie_and_unlocks_the_api(self):
        response = self.login()

        self.assertEqual(response.status_code, 204, response.text)
        sid = response.cookies.get(SESSION_COOKIE)
        self.assertTrue(sid)

        session = self.client.get("/api/auth/session")
        self.assertEqual(session.status_code, 200)
        self.assertTrue(session.json()["authenticated"])

    def test_cookie_is_httponly_and_samesite(self):
        cookie_header = self.login().headers["set-cookie"].lower()

        self.assertIn("httponly", cookie_header)
        self.assertIn("samesite=lax", cookie_header)

    def test_wrong_password_is_rejected_without_explaining(self):
        response = self.login("wrong")

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"], "口令错误")
        self.assertNotIn(SESSION_COOKIE, response.cookies)

    def test_empty_password_looks_exactly_like_a_wrong_one(self):
        wrong = self.login("wrong")
        empty = self.login("")

        self.assertEqual(empty.status_code, wrong.status_code)
        self.assertEqual(empty.json(), wrong.json())

    def test_repeated_failures_do_not_leave_a_session_behind(self):
        for _ in range(3):
            self.login("wrong")

        self.assertEqual(self.auth.active_sessions(), 0)


class Logout(ApiTestCase):
    def test_logout_makes_the_old_cookie_useless_right_away(self):
        self.login()
        stolen_cookie = self.client.cookies.get(SESSION_COOKIE)

        self.assertEqual(self.client.get("/api/auth/session").status_code, 200)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)

        # 重放退出前抓到的 cookie：必须 401，而不是"客户端删了就算退出"
        replay = TestClient(self.app)
        replay.cookies.set(SESSION_COOKIE, stolen_cookie)
        self.assert_unauthenticated(replay.get("/api/auth/session"))

    def test_logout_without_login_is_harmless(self):
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)

    def test_expired_session_is_rejected(self):
        self.login()
        self.clock.now += 61.0

        response = self.client.get("/api/auth/session")

        self.assertEqual(response.status_code, 401)


class StartupRequiresPassword(ApiTestCase):
    def test_missing_env_password_refuses_to_start(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                create_app()
        self.assertIn("ADMIN_PASSWORD", str(ctx.exception))

    def test_password_comes_from_the_environment(self):
        env = {"ADMIN_PASSWORD": "from-env"}
        with mock.patch.dict("os.environ", env, clear=True):
            app = create_app()

        client = TestClient(app)
        self.assertEqual(client.post("/api/auth/login", json={"password": "from-env"}).status_code, 204)

    def test_session_hours_must_be_positive(self):
        env = {"ADMIN_PASSWORD": "x", "ADMIN_SESSION_HOURS": "0"}
        with mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaises(RuntimeError):
                create_app()


if __name__ == "__main__":
    unittest.main()
