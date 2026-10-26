# 架构设计

> 本文是**唯一的设计真源**：系统由什么组成、数据怎么流动、为什么这么切、边界在哪里。
> 不含进度计划，不含文献综述（依据见附录 A）。

## 1. 设计原则

1. **模型只产出中间产物，分数由代码计算。** 模型输出"逐采分点的判定 + 原文证据"，总分由服务端按权重求和。
2. **每一处不确定性都要被量化。** 采样次数、判定分歧、证据是否可定位——都要变成可记录的数值，而不是"相信模型"。
3. **接口缝先于实现。** 模型供应商、存储、任务执行形式都通过窄接口隔离，未定项不阻塞编码。
4. **失败必须显式。** 解析失败、证据缺失、超长截断都要留下标记并转人工，**不允许静默给 0 分**。

## 2. 一次评分的数据流

八步。**只有第 4 步是不确定的**，其余都是可单测的确定性代码。

| # | 步骤 | 输入 | 处理 | 输出（数据形态） | 失败分支 |
| --- | --- | --- | --- | --- | --- |
| 1 | 载入题目与采分点 | rubric JSON | 校验：`Σweight == total_score`、id 唯一、等级取值合法 | 内存对象 `Rubric` | 校验失败 → 拒绝入库，报出具体字段 |
| 2 | 预处理答卷 | 原始答案文本 | 清洗、全半角/繁简规范化（保留原文）、脱敏、超长截断、注入词检测 | `raw_text` + `normalized_text` | 超长 → 分段并打 `truncated` 标记 |
| 3 | 组装提示词 | Rubric + 答案 | 稳定前缀（题目 + 采分点 + 参考解）在前，答案包裹在后；按模式选模板 | 纯文本消息列表 | 模板缺失 → 启动期报错，不等到运行时 |
| 4 | 调用模型 | 消息列表 | 经 `GradingProvider` 调 API，要求 JSON 输出；重试与超时在此层 | JSON 字符串 | 空响应/解析失败 → 重试 ≤3 次 → `ENGINE_ERROR` |
| 5 | 解析与接地校验 | JSON + 原文 | 校验字段完整性与等级合法性；把 `evidence` 回原文定位（子串 → LCS 容错） | 逐条判定对象，含 `span` 与 `grounded` | 定位失败 → 该点判 0 并标 `UNGROUNDED` |
| 6 | 多次采样聚合 | n 份判定 | 逐采分点取中位数等级，计算离散度与置信度 | 聚合结果 + `confidence` | 样本间全不一致 → 强制转人工 |
| 7 | 服务端算分与校准 | 聚合后的等级 | 按 `level→score` 加权求和、扣分项封顶、clamp；必要时套校准映射 | 最终分 + 是否校准 | 无校准数据 → 跳过，保留原始分 |
| 8 | 路由与落库 | 最终分 + 置信度 | 判定自动通过/人工复核，写入记录（不含身份信息） | 数据库行 + `route` | 写库失败 → 返回结果但记审计告警 |

**三个关键性质：**

- **可替换**：第 4 步被 `GradingProvider` 包住，换供应商只改一个实现类。
- **可复现**：`rubric_version + prompt_version + model + params + answer_hash` 决定一条记录，命中即复用。
- **可追责**：每条判定都带证据与字符偏移，能回到原文第几个字。

## 3. 模块划分

```text
backend/
├── app/
│   ├── main.py               # FastAPI 装配：挂路由、异常处理、启动校验
│   ├── api/
│   │   ├── routes.py         # 提交 / 查询 / 复核 三组接口
│   │   └── schemas.py        # 请求响应模型（pydantic）
│   ├── core/
│   │   ├── rubric.py         # 4.1 的校验与加载
│   │   ├── normalize.py      # 规范化 + 原文偏移映射
│   │   ├── prompt.py         # 第 3 步的模板与组装
│   │   ├── grounding.py      # 第 5 步的证据定位
│   │   ├── aggregate.py      # 第 6 步的中位数/离散度/置信度
│   │   ├── calibrate.py      # 第 7 步的保序回归
│   │   └── routing.py        # 第 8 步的自动通过判定
│   ├── providers/
│   │   ├── base.py           # GradingProvider 协议
│   │   ├── registry.py       # 模型能力表（JSON 输出 / 确定性采样 / 上下文上限）
│   │   └── <vendor>.py       # 各供应商实现
│   └── store.py              # 存储接口（开发期 SQLite）
├── tests/                    # 纯函数单测 + 合成用例回归
└── scripts/                  # check（不调模型）/ run（真调模型）
```

**依赖方向只能自上而下，禁止反向引用：**

| 层 | 模块 | 允许依赖 |
| --- | --- | --- |
| 入口 | `main.py`、`api/` | 以下全部 |
| 编排 | `pipeline.py`（后续加入）、`store.py` | core、providers |
| 领域逻辑 | `core/*` | 仅标准库 + pydantic |
| 适配 | `providers/*`、`store.py` | 外部 SDK |
| 工具 | `scripts/*`、`tests/*` | 全部 |

`core/` 必须不 import 任何厂商 SDK，也不读环境变量——这是"可单测、可替换"的物理保证。

## 4. 核心数据结构

### 4.1 评分标准（rubric）

```json
{
  "question_id": "Q-01",
  "stem": "简述 TCP 三次握手的过程与目的。",
  "reference_answer": "……",
  "total_score": 10,
  "items": [
    {
      "id": "P1",
      "desc": "客户端发送 SYN 及初始序列号",
      "weight": 3,
      "levels": [
        { "level": 1.0, "score": 3.0, "criterion": "明确 SYN=1、seq=x 并说明目的" },
        { "level": 0.5, "score": 1.5, "criterion": "只提到发送连接请求" },
        { "level": 0.0, "score": 0.0, "criterion": "未提及" }
      ],
      "must_hit": true,
      "acceptable": ["同义表述：SYN 报文 / 同步标志位"]
    }
  ],
  "deductions": [
    { "id": "D1", "desc": "与四次挥手混淆", "penalty": -2, "cap": -2 }
  ],
  "rules": { "allow_inference": false, "synonym_ok": true, "ignore_length": true }
}
```

**校验规则（服务端强制）：**

| 规则 | 违反后果 |
| --- | --- |
| `Σ weight == total_score`（浮点容差 1e-9） | 拒绝入库 |
| 采分点 id 与扣分项 id 全局唯一 | 拒绝入库 |
| 每个 item 至少两个等级，且分数随等级单调不减 | 拒绝入库 |
| 每级 `score ∈ [0, weight]` | 拒绝入库 |
| `Deduction.cap ≤ 0` 且 `penalty ≥ cap` | 拒绝入库 |

### 4.2 模型输出（一次采样）

```json
{
  "items": [
    { "id": "P1", "hit": true, "level": 0.5, "evidence": "客户端首先发送 SYN 报文", "reason": "提到 SYN 但未说明 seq" }
  ],
  "deductions": [ { "id": "D1", "hit": false, "evidence": "" } ],
  "comment": "总体评语（面向学生，不泄露参考答案）"
}
```

模型**不允许**输出总分、满分或任何派生字段——这些由第 7 步计算。

### 4.3 判定与聚合对象

| 对象 | 关键字段 | 说明 |
| --- | --- | --- |
| `ItemJudgement` | `item_id`、`level`、`hit`、`evidence`、`span`、`grounded`、`reason` | 一次采样的单点判定 |
| `SampleJudgement` | `items[]`、`deductions[]`、`comment` | 一次采样的完整结果 |
| `Aggregate` | `levels{}`、`score`、`dispersion`、`confidence`、`sample_totals[]` | n 次采样聚合 |
| `RouteDecision` | `route`（auto/human）、`risk`、`reasons[]` | 第 8 步产出 |

### 4.4 存储表（开发期 SQLite，逻辑模型与库无关）

```text
questions(id, stem, type, total_score, status)
rubrics(id, question_id, version, schema_json, checksum, created_at)
answers(id, question_id, student_pseudo_id, raw_text, normalized_text, truncated)
grading_runs(id, answer_id, rubric_version, prompt_version, model, params_json,
             status, tokens_in, tokens_out, latency_ms, error)
item_results(id, run_id, rubric_item_id, level, hit, evidence, span_start, span_end,
             grounded, reason)
aggregated_results(id, answer_id, levels_json, raw_score, calibrated_score,
                   dispersion, confidence, grounding_rate, route, calibration_version)
reviews(id, aggregated_result_id, reviewer_id, final_score, decision, comment)
calibration_models(id, version, method, params_json, train_size, metrics_json)
```

`student_pseudo_id` 是脱敏占位符，不存姓名学号。审计日志与申诉表留到有真实用户时再加。

## 5. 关键设计决策

| # | 决策点 | 备选 | 选择 | 代价（必须承认） |
| --- | --- | --- | --- | --- |
| D1 | 评分单位 | 整体总分 / 按采分点 | **采分点** | 评分标准必须先人工结构化，成本前移 |
| D2 | 谁算分数 | 模型输出 / 服务端加权 | **服务端** | 需维护等级-分值映射，灵活性下降 |
| D3 | 可信性来源 | 让模型写理由 / 校验证据可定位 | **可定位证据** | 可能误杀正确判定；短证据定位不稳 |
| D4 | 稳定性来源 | 调参求确定 / 多次采样 | **采样聚合** | N 倍成本与延迟；只压随机误差 |
| D5 | 自动化边界 | 全自动 / 置信度路由 | **路由** | 需人工池与阈值校准，且要证明没牺牲质量 |

**与成熟实现的关系**：开源项目 LLM Grader（NYU 课程在用）同样把分数计算放在服务端、由模型只出逐条判定。
这印证 D2 不是臆断。但它的目标是课堂即时反馈，**没有**采样聚合、证据定位校验、置信度与人工路由——
这四项是本系统的增量，也是第 7 节要验证的对象。

## 6. 失败与边界

| 情况 | 处理 | 用户可见性 |
| --- | --- | --- |
| 模型返回空内容 | 指数退避重试 ≤3 次 | 仍失败 → 标记人工复核 |
| JSON 不合法 / 缺采分点 | 重试；仍缺 → `INCOMPLETE` | 复核界面标红 |
| 等级不在允许集合 | 该条作废，按 0 分计 | 记录 `INVALID_LEVEL` |
| 证据无法定位 | 该点判 0，标 `UNGROUNDED` | 复核界面显示"证据未找到" |
| 答案超长被截断 | 分段评分后合并，或首尾保留 | 记录 `truncated=true` |
| 答案含注入指令 | 分隔符包裹 + system 声明 + 记录 | 抽检队列 |
| 429 / 5xx / 超时 | 重试（含抖动） | 状态列可见 |
| 400 / 401 / 402 | 不重试，直接失败 | 明确错误码，不吞异常 |

## 7. 验证方式

### 7.1 没有真实答卷时（当前阶段）

用**合成用例**先立回归：每条用例 = 答案文本 + 期望总分 + 每条采分点的期望等级。

- `check`：**不调模型**，只校验用例与 rubric 的一致性（qtag 是否存在、id 是否失效、期望值是否超出取值域）。
- `run`：真调模型，比对期望。**每套 rubric 至少有一条"正确答案"用例**——否则容易写出"能抓所有错、也判错正确"的规则。

这一步让"没有数据"不再是阻塞项：语料可以后补，回归测试先跑起来。

### 7.2 有标注数据后

| 对照组 | 配置 | 回答的问题 |
| --- | --- | --- |
| B0 | 一段提示词直接要总分 | 系统比朴素做法好在哪 |
| B2 | 逐点判定、无证据校验 | 结构化本身带来多少 |
| B3 | B2 + 证据接地 | 接地是否真的拦住了编造 |
| B5 | B3 + 采样 + 校准 + 路由 | 完整系统的上限 |

指标：QWK（主）、MAE、ICC(2,1)、grounding rate、自动通过率与通过样本上的人机一致率。
**必须同时报教师之间的一致性作为参照系**，否则"准确率 0.8"没有意义。

### 7.3 结论的边界（不许夸大）

- 接地只证明"引用了原文"，不证明"判定正确"。
- 多采样只压随机误差，压不了系统性偏差。
- 提示注入只能缓解，不能消除。
- B0 的对比是设计合理性的**唯一**证据，不能省。

## 8. 待定项

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 语言 / 框架 | **已定** Python 3.10+ / FastAPI | 只承担 API 层 |
| 服务形态 | **已定** 单体 | 课题规模不需要拆服务 |
| LLM 供应商与参数 | **待补充** | 由 `providers/` 隔离；接入时把能力写进 `registry.py` |
| 存储 | 待定 | 开发期 SQLite；表结构见 4.4，与库无关 |
| 任务执行 | 待定 | 进程内后台任务起步，压力上来再换 worker |
| 前端 | 待定 | 复核界面是必需项，技术栈未定 |
| 部署 | 待定 | 本地演示优先 |

## 附录 A 依据（精简版）

| 编号 | 文献 | 支撑什么 | 等级 |
| --- | --- | --- | --- |
| A1 | Wang et al., *Self-Consistency Improves CoT Reasoning*, ICLR 2023. arXiv:2203.11171 | 多采样聚合 | 迁移 |
| A2 | Zheng et al., *Judging LLM-as-a-Judge*, NeurIPS 2023. arXiv:2306.05685 | 单次评判不可靠、长度/位置偏差 | 直接 |
| A3 | Liu et al., *G-Eval*, EMNLP 2023. arXiv:2303.16634 | 逐项表单式评分优于整体打分 | 迁移 |
| A4 | Cohen, *Weighted kappa*, Psychological Bulletin 70(4), 1968 | QWK 指标 | 直接 |
| A5 | Shrout & Fleiss, *Intraclass correlations*, Psychological Bulletin 86(2), 1979 | ICC 信度 | 直接 |
| A6 | Niculescu-Mizil & Caruana, *Predicting Good Probabilities*, ICML 2005 | 保序回归校准 | 直接 |
| A7 | Yue et al., *Automatic Evaluation of Attribution*, EMNLP Findings 2023. arXiv:2305.06311 | 模型会编造引用 | 迁移 |
| A8 | Geifman & El-Yaniv, *Selective Classification*, NeurIPS 2017. arXiv:1705.08500 | 弃权/路由的理论框架 | 迁移 |
| A9 | OWASP, *Top 10 for LLM Applications 2025*: LLM01 Prompt Injection | 注入威胁 | 直接 |

> 等级含义：**直接** = 论文研究的就是这个问题；**迁移** = 相邻场景已验证，用于本场景需自行验证。
> 写出处时不要写成"本文提出"——这些都是成熟方法，本课题的价值在组合与工程落地。

## 附录 B 从参考实现借鉴与放弃

| 借鉴 | 放弃 |
| --- | --- |
| 服务端计算派生分数，模型只出原始字段 | XML + XSD 作唯一真源（改用 JSON + pydantic） |
| 评分回归测试：check 免费 / run 花钱 | Flask + Jinja 服务端渲染（已定 FastAPI + 前后端分离） |
| 模型能力注册表（是否支持 JSON 输出、确定性采样） | MCP 作者助手（工作量大，列为未来工作） |
| 提示词按"评分模式 × 有无采分点"拆模板 | 模板矩阵全量铺开（先做 4 格） |
| 匿名 session、不存学生身份 | 导出第三方成绩系统的整套链路 |
