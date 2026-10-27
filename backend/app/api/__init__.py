"""HTTP 接口层：路由、请求响应模型、认证依赖。

按架构 §3，这一层可以依赖下面所有层；反过来不行。
"""

from .routes import register

__all__ = ["register"]
