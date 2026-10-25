# 领域文档

说明工程类 skill 在探索代码库时，应如何消费本仓库的领域文档。

## 探索之前先读这些

- 仓库根目录的 **`GLOSSARY.md`**；或
- 若存在 **`GLOSSARY-MAP.md`**：它指向每个上下文各自的 `GLOSSARY.md`，读取与当前主题相关的若干份。
- **`docs/adr/`**：读取与即将改动的区域相关的 ADR。多上下文仓库还要检查 `src/<context>/docs/adr/` 中的上下文级决策。

如果上述文件不存在，**静默继续**。不要提示缺失，也不要建议预先创建。
`/domain-modeling`（经由 `/grill-with-docs` 与 `/improve-codebase-architecture` 触达）
会在术语或决策真正确定时惰性创建它们。

## 文件结构

本仓库为单上下文布局：

```text
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-....md
│   └── 0002-....md
└── backend/
```

多上下文布局（根目录存在 `GLOSSARY-MAP.md` 时）：

```text
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← 系统级决策
└── src/
    ├── ordering/
    │   ├── GLOSSARY.md
    │   └── docs/adr/                  ← 上下文级决策
    └── billing/
        ├── GLOSSARY.md
        └── docs/adr/
```

## 使用术语表的词汇

当输出中要命名某个领域概念（issue 标题、重构提案、假设、测试名），使用 `GLOSSARY.md`
中定义的术语，不要滑向术语表明确回避的同义词。

若需要的概念尚未收录，这本身是个信号：要么你在发明项目并不使用的语言（重新考虑），
要么确实存在缺口（记下来交给 `/domain-modeling`）。

## 标出 ADR 冲突

若你的输出与既有 ADR 相矛盾，显式指出，而不是静默覆盖：

> _与 ADR-0007（事件溯源的订单）冲突，但值得重开，因为……_
