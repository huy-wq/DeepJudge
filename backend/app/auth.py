"""管理端认证：一个共享口令 + 服务端会话登记。

**为什么不是无状态签名 cookie**（架构 §5.8 最初写的是"签名 cookie"）：
验收要求"退出后原凭据立即不可用"。签名只能证明"这个 cookie 是我发的"，
不能证明"它还没被撤销"——无状态令牌在退出后仍然有效，只要有人把旧 cookie 留着。
所以必须有服务端状态：每个会话一行登记，退出即删。架构 §5.8 已按此更正。

**为什么放在内存**：单用户、单进程、本地演示。代价是重启即全部失效（重新登录即可），
多 worker 之间也不共享。要上多进程时换成共享存储，接口不用变。

**口令只从环境变量来**：这里不接受"默认口令"，构造时为空就直接报错——
宁可启动失败，也不要跑起来一个谁都能进的管理端。
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Callable

# Cookie 名与属性集中在这里：路由层只管把 sid 塞进去，不自己拼 Cookie 头。
SESSION_COOKIE = "deepjudge_admin"
SESSION_TTL_SECONDS = 8 * 3600


class AuthError(Exception):
    """认证失败。`code` 给程序分支，`message` 给日志与响应用。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class Session:
    sid: str
    expires_at: float


class AdminAuth:
    """共享口令 + 会话登记表。

    `clock` 可注入：过期逻辑不靠 `sleep` 测，而是把时间拨过去。
    """

    def __init__(
        self,
        password: str,
        *,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not password:
            raise ValueError(
                "ADMIN_PASSWORD 未设置：管理端不能在没有口令的情况下运行"
            )
        if ttl_seconds <= 0:
            raise ValueError("会话有效期必须大于 0")

        self._password = password
        self._ttl = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, float] = {}

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def login(self, attempt: str | None) -> Session:
        """口令正确则登记一个新会话；错误则抛 `AuthError`。

        用 `compare_digest` 而不是 `==`：后者会按首个不同字节提前返回，
        让"口令前几位对了"变成可测量的事实。
        """
        given = attempt if isinstance(attempt, str) else ""
        if not hmac.compare_digest(given.encode("utf-8"), self._password.encode("utf-8")):
            raise AuthError("INVALID_PASSWORD", "口令错误")

        sid = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._ttl
        self._sessions[sid] = expires_at
        return Session(sid=sid, expires_at=expires_at)

    def verify(self, sid: str | None) -> Session:
        """校验会话：缺失、伪造、过期、已退出，一律抛 `AuthError`。"""
        if not sid:
            raise AuthError("NO_SESSION", "未登录")

        expires_at = self._sessions.get(sid)
        if expires_at is None:
            # 伪造的 sid 与已退出的 sid 走同一条路：对外都是"未登录"，
            # 不区分"没发过"和"发过但撤了"，免得成为探测手段。
            raise AuthError("NO_SESSION", "未登录")

        if expires_at <= self._clock():
            del self._sessions[sid]
            raise AuthError("EXPIRED", "会话已过期，请重新登录")

        return Session(sid=sid, expires_at=expires_at)

    def logout(self, sid: str | None) -> bool:
        """撤销会话。幂等：没登录过、已经撤过、sid 是伪造的，都不报错。"""
        if not sid:
            return False
        return self._sessions.pop(sid, None) is not None

    def active_sessions(self) -> int:
        """当前有效会话数。给健康检查与测试用。"""
        now = self._clock()
        expired = [sid for sid, expires_at in self._sessions.items() if expires_at <= now]
        for sid in expired:
            del self._sessions[sid]
        return len(self._sessions)
