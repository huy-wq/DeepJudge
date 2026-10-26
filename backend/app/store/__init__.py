"""存储层公开接口。

外部只应 `from app.store import Store, RunMeta, StoredEvaluation`，
不要直接 import `repository` / `tables` —— 表结构是内部实现。
唯一例外是 `metadata`：Alembic 需要它来对照模型，所以显式导出。
"""

from .repository import DEFAULT_PSEUDO_ID, RunMeta, Store, StoredEvaluation
from .tables import metadata

__all__ = ["DEFAULT_PSEUDO_ID", "RunMeta", "Store", "StoredEvaluation", "metadata"]
