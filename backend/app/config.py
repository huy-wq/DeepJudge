"""极简 .env 读取。

不引入 python-dotenv：只需要 KEY=VALUE 与 # 注释两种语法，
已经存在的环境变量优先（方便 CI 或临时覆盖）。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path) -> None:
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def load_project_env(start: str | Path | None = None) -> None:
    """从当前目录向上三层内找 .env，先找到的先加载。"""
    base = Path(start or Path.cwd()).resolve()
    for candidate in [base, *base.parents][:3]:
        load_env_file(candidate / ".env")
