"""演示种子数据：一份 human、一份 auto，重复执行不重复写。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import seed_demo

from app.store import Store
from tests.support import create_schema


class SeedDemo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.url = f"sqlite:///{Path(self.dir.name) / 'demo.db'}"
        store = Store.from_url(self.url)
        create_schema(store)
        store.dispose()
        patcher = mock.patch("scripts.seed_demo.load_project_env", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def seed(self) -> int:
        return seed_demo.main(["--database-url", self.url])

    def test_seeds_one_human_and_one_auto_result(self):
        self.assertEqual(self.seed(), 0)
        store = Store.from_url(self.url)
        try:
            human = store.list_results(route="human", pending=True)
            auto = store.list_results(route="auto", pending=True)
            self.assertEqual([row["pseudo_id"] for row in human], ["S-DEMO-01"])
            self.assertEqual([row["pseudo_id"] for row in auto], ["S-DEMO-02"])
            self.assertEqual(store.count_answers("Q-01"), 2)
        finally:
            store.dispose()

    def test_refuses_to_seed_twice(self):
        self.assertEqual(self.seed(), 0)

        self.assertEqual(self.seed(), 2)


if __name__ == "__main__":
    unittest.main()
