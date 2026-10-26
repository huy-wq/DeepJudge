# 参考项目拆解：llmgrader

> 目的：把 [sdrangan/llmgrader](https://github.com/sdrangan/llmgrader) 拆到"能照着做决定"的粒度——
> 它做对了什么、可以抄什么、它没有的东西正好是你要补的。
>
> 阅读建议：先看第 2 节（它解决什么问题）和第 6 节（五个值得借鉴的设计），
> 再看第 7 节（它的空白就是你的设计空间），最后看第 8 节（落地建议）。

## 0. 一句话概括

**NYU 电子工程课的 LLM 自动评分引擎**：讲师用 XML 写题目、参考答案和评分标准，学生在网页提交答案（可附图片），
模型按标准逐条判分并给出反馈，成绩最终回传 Gradescope。

它是真实在用的课程系统（Introduction to Hardware Design，NYU 硕士课），不是演示项目。

## 1. 基本信息

| 项 | 值 |
| --- | --- |
| 仓库 | `sdrangan/llmgrader` |
| 作者 | Sundeep Rangan（NYU ECE 教授，NYU Wireless 主任） |
| 语言 / 框架 | Python 3.10+ / Flask（服务端渲染 + Jinja 模板） |
| 依赖 | `openai`（Responses API）、`pydantic` v2、`xmlschema`、`mcp`、`pandas`、`cryptography` |
| 存储 | SQLite（评分记录） |
| 规模 | 排除 `.venv` 约 4.4 万行；69 个测试文件；`routes/api.py` 17 个接口 |
| 许可证 | **PySilicon Research License**：非商用研究/教学可用，需保留版权声明；商用需授权 |
| 最后提交 | 2026-10-24（活跃开发） |

### 许可的实操含义

- 你的课题是学术用途 → **可以用、可以改**，但必须保留版权声明，并在论文/报告里注明来源。
- **不能商用**（包括以后创业或给机构做付费部署）。
- 最稳妥的做法：**只借鉴设计，不复制代码**。本文就是按这个原则写的。
  如果确实要复制，就把 `LICENSE` 一并保留，并在 README 注明"部分代码来自 llmgrader（PySilicon Research License）"。

## 2. 它解决什么问题

传统自动评分器只能判"结果对不对"（数值、正则、单测）。这个项目针对的是**过程也要算分**的工程课题目：

- 多步推导（微积分、电路分析）
- 设计权衡（为什么选这个参数）
- 近似与假设
- 开放式论证

它和你的课题是同一类问题：**主观题的评分标准如何被机器可靠执行**。区别在于它偏"课程工具"，你偏"可追责的评分系统"。

## 3. 架构：五块拼起来

```text
课程包（XML，讲师写的唯一真源）
   │  unit → question → parts → rubrics
   ▼
Web 门户（Flask + Jinja）
   │  学生作答 / 讲师管理 / 成绩展示
   ▼
评分服务（services/grader.py，核心 1938 行）
   │  组装提示词 → 调模型 → 校验 → 派生字段
   ▼
SQLite（评分记录，匿名 session）
   │
   ├──→ Gradescope 导出（含不调 LLM 的离线 autograder）
   └──→ MCP 作者助手（Course Builder，辅助讲师写 XML）
```

几个值得注意的模块边界：

| 文件 | 职责 | 对应你项目里的位置 |
| --- | --- | --- |
| `services/unit_parser.py`（1558 行） | XML → 内存结构，schema 校验 | 你的 rubric 加载与校验 |
| `services/prompt.py`（844 行） | 按模式选模板、格式化 rubric | 你的 `prompt.py` |
| `services/grader.py`（1938 行） | 调用、校验、派生、聚合部分结果 | 你的 `pipeline.py` |
| `services/gradetests.py`（3025 行） | 评分回归测试的执行器 | 你的评测脚本 |
| `services/models.py` | 模型注册表（能力、价格、档位） | 你的 provider 注册表 |

## 4. 数据模型：用 XML 而不是提示词

讲师写的不是 prompt，是结构化 XML。这是它和"随手写个提示词"最本质的区别。

```xml
<unit id="calculus" title="Unit 1: Calculus Review">
  <question qtag="Exponential derivative" preferred_model="simple">
    <question_text><![CDATA[ <p>...</p> ]]></question_text>
    <solution><![CDATA[ ...参考解析... ]]></solution>
    <partial_credit>false</partial_credit>
    <parts><part><part_label>all</part_label><points>10</points></part></parts>
    <rubrics>
      <item id="taking_logarithm" condition_type="positive" action="fail">
        <display_text>Taking logarithm</display_text>
        <condition>学生取对数后隐函数求导……</condition>
        <notes>若学生用链乘法则可跳过本条</notes>
      </item>
      <item id="polynomial_confusion" condition_type="negative" action="fail">
        <condition>把指数函数当多项式求导，如 y' = x·a^(x-1)</condition>
      </item>
      <group type="one_of">
        <id>taking_logarithm</id><id>exponential_form</id>
      </group>
    </rubrics>
  </question>
</unit>
```

关键字段的用意：

| 字段 | 作用 | 对你的启发 |
| --- | --- | --- |
| `condition_type` = positive / negative | 区分"该做到"与"不该做" | 负向采分点（常见误解）单独建模，比统一"扣分项"更可判 |
| `action` = fail / feedback | 决定命中后是否直接判失败 | 分"致命项"与"提示项"，避免rubric 太脆 |
| `group type="one_of"` | 一组等价方法只认其一 | **一题多解**必须显式建模，否则模型会因选错方法误判 |
| `notes` | 豁免条件、替代方法说明 | 大量"例外"写在 notes 里，而不是塞进 condition |
| `scored="false"` | 只给反馈不计分 | 评语与分数解耦，值得借鉴 |
| `partial_credit` | 二值 / 部分给分两种模式 | 两种模式配两套 rubric 语义与提示词 |

它的 rubric 编写规范（文档原话，翻译）：**原子（可独立判定）、可观察（能指向具体证据）、不重叠（同一错误不重复扣）、判断正确性而非文风**。
这四条是 rubric 质量的检验标准，你可以直接写进 `architecture.md` 的 rubric 质检一节。

## 5. 评分链路：七步

1. **组装提示词**：按「评分模式 × 部分 × 有无 rubric」选择模板，把 rubric 格式化成 `id / part / point_adjustment / display_text / condition / notes` 的列表。
2. **调用模型**：OpenAI Responses API，`text.format = {"type": "json_object"}`；支持时设 `temperature=0`，模型不支持则**整个参数不传**。
3. **解析校验**：`GraderRawResult`（pydantic）做宽松校验——`extra="forbid"` 但会丢弃 `rubric_eval` 里的杂项条目，而不是整单失败。
4. **后端派生**：`grade_post_process()` 补齐 `max_points`、`point_parts`、聚合 `result`（全 pass → pass；全 fail → fail；含 error → error；其余 partial），并做 `clamp(0, max)`。
5. **逐条证据**：`rubric_eval[id] = {evidence, point_awarded, result}`，evidence 写给学生的两三句话。
6. **落库**：SQLite 记录题目、答案、参考解、rubric、结果、模型、时延、token 数——**不含任何身份信息**。
7. **展示 / 导出**：网页展示逐条命中；需要时导出 Gradescope 格式，并提供一个**不调用 LLM** 的离线 autograder。

## 6. 五个值得借鉴的设计

### D1. 后端算派生分数，模型只出原始字段

提示词里明确写："后端会计算 `max_point_parts`、`max_points`、聚合结果等派生字段，你只返回被要求返回的字段。"

**这和你 architecture.md 里"总分由服务端裁决"是同一个原则**。它已经在真实课堂里验证过可行——这条可以在你的设计报告里作为"与成熟实现一致"的佐证。

### D2. 评分回归测试（最值得抄）

它给每道题配一份 `unit_test` XML，用例是"答案 + 期望结果 + 每条 rubric 的期望值"：

```xml
<case id="log_method_correct" qtag="Exponential derivative">
  <description>满分对照：用取对数方法求解。</description>
  <solution><![CDATA[ 两边取对数…… ]]></solution>
  <expected_result>pass</expected_result>
  <expected_rubrics>
    <item id="taking_logarithm" expect="pass"/>
    <item id="final_answer" expect="pass"/>
  </expected_rubrics>
</case>
```

两种执行模式，这个划分很聪明：

- `llmgrader_test check`：**不花钱**，只校验用例本身——qtag 是否写错、rubric id 是否失效、分值区间是否永远不可满足。
- `llmgrader_test run`：**真调模型**，验证评分行为。

它还专门写了理由："每套 rubric 至少要有一个正确答案的对照用例——很容易写出一套能抓住所有错误、同时也判错正确答案的 rubric。"

**这条对你直接有用**：你正愁"没有真实答卷怎么验证"，答案是——先写"期望判分"的合成用例，语料可以后补，回归测试先立起来。

### D3. 模型注册表 + 能力标志

`ModelSpec` 里除了价格和上下文，还记录 `supports_temperature`、`supports_images`、`supports_web_search`、`tier`。
提示词/调用层根据标志决定参数，而不是硬编码。

**你现在 API 未定，正好需要一个这样的注册表**：`deepseek-flash`、`deepseek-v4-pro` 只是两行配置；以后换供应商也只需加一条。
特别地，它明确处理了"某些模型拒收 `temperature` 参数，必须整个省略而不是传 0"——这是很实际的坑。

### D4. 提示词按"模式矩阵"拆模板

不是一个大 prompt，而是 `{binary, partial} × {single, multi} × {有 rubric, 无 rubric}` 的组合模板，再加 `JSON_REQUIREMENT` 公共约束。

好处：每种情况的指令都短、具体、无歧义；坏处：模板数量膨胀，维护成本高。**你可以先做两三个模板，把矩阵留作扩展点。**

### D5. 隐私：匿名 session + 不存学生密钥

- 评分记录只关联 8 位随机 session UUID，不存姓名/学号/邮箱。
- 学生的 API key 只存在浏览器端，不上服务器。
- 文档里明确写"答案文本本身可能含身份信息，这是学生自己的责任"。

这条对你的合规章节很有参考价值——尤其是"匿名 ID + 数据最小化"的表述方式。

## 7. 它没有的东西（你的设计空间）

我逐项搜过代码，以下能力**不存在**：

| 缺失 | 它的做法 | 你的方案 |
| --- | --- | --- |
| 多次采样 / 一致性聚合 | 单次调用，靠 `temperature=0` 求确定性 | 多采样 + 中位数聚合 + 离散度（architecture 3.6） |
| 置信度 | 无 | 由离散度 + 接地率计算（3.6） |
| 人工复核路由 | 无 | 阈值路由 + 风险排序（3.8） |
| 证据可定位校验 | 只有 `evidence` 文本，不校验是否真出自学生答案 | 子串/LCS 定位，`UNGROUNDED` 判 0（3.5） |
| 分数校准 | 无 | 保序回归（3.7） |
| 一致性指标 | 无 QWK / ICC | 指标体系（evaluation.md 第 3 节） |
| 批量 / 并发 / 成本控制 | 面向单课程小规模 | 任务队列（形态待定） |

这不是它的缺点——它的目标是"课堂即时反馈"，不是"可作正式成绩的评分系统"。
**但对你的课题来说，这七行就是差异化**：你的设计报告可以写成"在 llmgrader 这类成熟实现的基础上，补足可靠性闭环"。

> 注意措辞：不要写"llmgrader 缺少这些"，而要写"面向正式成绩场景，还需要补充……"。前者是贬低，后者是定位差异。

## 8. 落地建议

### 可以直接借鉴（不复制代码）

1. **rubric 的字段设计**：`condition_type` / `action` / `group one_of` / `notes` / `scored` 这五个概念，改成你的 JSON schema。
2. **测试用例 DSL**：`case = {答案, 期望结果, 每条 rubric 的期望}`，先跑不花钱的 check，再跑真调用的 run。
3. **模型能力注册表**：`supports_temperature` 这类标志位，正好解决你 API 未定的问题。
4. **提示词模式矩阵**：先做 binary/partial × 有/无 rubric 四格。
5. **隐私表述**：匿名 UUID + 数据最小化。

### 不建议照搬

| 它的做法 | 为什么不建议 |
| --- | --- |
| Flask + Jinja 服务端渲染 | 你已定 FastAPI + 前后端分离；Jinja 会把 UI 和业务耦合 |
| XML 作为唯一真源 + XSD | JSON + pydantic 更贴合 FastAPI 生态，也更好版本化 |
| OpenAI Responses API | DeepSeek 走 Chat Completions 兼容接口，`response_format` 用法不同，需要适配层 |
| MCP 作者助手（Course Builder） | 工作量极大，对毕设不是必需项；可作为"未来工作"提及 |
| 844 行的 `prompt.py` 模板矩阵 | 先做最小集合，别一上来铺满矩阵 |

### 模块映射（照着搭骨架）

```text
llmgrader/                      DeepJudge/
├── services/unit_parser.py  →  backend/app/core/rubric.py      # schema → 对象 + 校验
├── services/prompt.py       →  backend/app/core/prompt.py      # 模板 + 组装
├── services/grader.py       →  backend/app/core/pipeline.py    # 调用/校验/派生
│                               backend/app/providers/*.py      # 供应商适配
├── services/models.py       →  backend/app/providers/registry.py
├── services/gradetests.py   →  backend/tests/ + scripts/run_tests.py
└── services/portal_storage.py → backend/app/store.py           # 先 SQLite
```

## 9. 引用与本课题的定位表述

论文/设计报告里可以这样写（避免"借鉴"变"抄袭"）：

> 现有开源实现 LLM Grader（Rangan, 2026）采用结构化 rubric 与后端派生分数的设计，在课程场景中验证了
> "模型只做逐条判定、分数由服务端计算"的可行性。本文在其基础上，针对正式成绩场景补充了
> 证据可定位校验、多采样一致性、置信度路由与分数校准，并给出可量化的验证结果。

需在参考文献中列出仓库地址、作者与版本（commit）。

---

## 附：如果你想深入看

| 想了解 | 看哪里 |
| --- | --- |
| rubric 怎么写 | `docs/admin/buildcourse/rubrics.md` |
| 测试用例 DSL | `docs/admin/buildcourse/gradetests.md` + `example_repo/unit1/tests/` |
| 整体架构 | `docs/overview/architecture.md` |
| 隐私设计 | `docs/overview/dataprivacy.md` |
| 评分实现 | `llmgrader/services/grader.py`（`grade()` 与 `grade_post_process()`） |
| 提示词 | `llmgrader/services/prompt.py`（`PROMPT_PREAMBLE`、模板字典） |

> 本机已 clone 到 `/home/huy/llmgrader`，可直接对照阅读；该目录不是本仓库的一部分，不要提交。
