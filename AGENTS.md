# DeepJudge

基于 DeepSeek 的主观试题评判研究与设计。课题总览见 `README.md`，技术方案与评测设计见
`docs/architecture.md`、`docs/evaluation.md`。

## 项目约定

- **所有项目文档使用中文撰写**。代码、命令、API 字段名、标识符保持英文。
- 学生答卷与评分结果属于敏感数据，不得提交入库（`.gitignore` 已覆盖 `data/`）。
- 提交信息使用中文，格式：`类型(范围): 说明`。

## Agent skills

### Issue tracker（issue 追踪）

Issue 与 spec 存放在本仓库的 GitHub Issues 中，统一通过 `gh` CLI 操作。详见 `docs/agents/issue-tracker.md`。

### Triage labels（分诊标签）

五个标准分诊角色名直接作为标签字符串。详见 `docs/agents/triage-labels.md`。

### Domain docs（领域文档）

单上下文布局：仓库根目录的 `GLOSSARY.md` 与 `docs/adr/`。详见 `docs/agents/domain.md`。
