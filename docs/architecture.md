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
| 5 | 解析与接地校验 | JSON + 原文 | 校验字段完整性与等级合法性；把 `evidence` 回原文定位（规范化后精确子串 → 局部窗口容错，允许少量字符偏差） | 逐条判定对象，含 `span` 与 `grounded` | 定位失败 → 该点判 0 并标 `UNGROUNDED` |
| 6 | 多次采样聚合 | n 份判定 | 逐采分点取中位数等级，计算离散度与置信度 | 聚合结果 + `confidence` | 样本间全不一致 → 强制转人工 |
| 7 | 服务端算分与校准 | 聚合后的等级 | 按 `level→score` 加权求和、扣分项封顶、clamp；必要时套校准映射 | 最终分 + 是否校准 | 无校准数据 → 跳过，保留原始分 |
| 8 | 路由与落库 | 最终分 + 置信度 | 判定自动通过/人工复核，写入记录（不含身份信息） | 数据库行 + `route` | 写库失败 → 返回结果但记审计告警 |

**三个关键性质：**

- **可替换**：第 4 步被 `GradingProvider` 包住，换供应商只改一个实现类。
- **可复现**：`rubric_version + prompt_version + model + params + answer_hash` 决定一条记录，命中即复用。
- **可追责**：每条判定都带证据与字符偏移，能回到原文第几个字。

### 2.1 采样聚合的具体规则

模型只输出每点的 `level`，**总分由后台用聚合后的等级重算**，不做总分平均。

设 3 个采分点，权重 3 / 4 / 3，n = 3：

| 采分点 | 权重 | 三次采样 level | 中位数 | 得分 | 离散度 |
| --- | --- | --- | --- | --- | --- |
| P1 | 3 | 1.0 / 0.5 / 1.0 | 1.0 | 3.0 | 0.5 |
| P2 | 4 | 0.5 / 0.5 / 0.0 | 0.5 | 2.0 | 0.5 |
| P3 | 3 | 1.0 / 1.0 / 1.0 | 1.0 | 3.0 | 0 |

- 聚合分 = 8.0；三次采样的总分分别为 8.0 / 6.5 / 6.0，极差 2.0
- 加权离散度 = (3×0.5 + 4×0.5 + 3×0) / 10 = 0.35
- 置信度 =（1 − 0.35）× 接地率 = 0.65（本例接地率为 1）
- 极差 2.0 > 1 → 判为需人工复核

**指标定义：**

- 单点离散度 = 该点等级极差 ÷ 该点等级跨度（0/0.5/1 的跨度为 1）。
- 整体离散度 = 按采分点分值加权；扣分项按扣分幅度折算后并入，最终截断到 1。
- 置信度 =（1 − 离散度）× 接地率。**全部未接地时置信度为 0**，即使多次采样完全一致。
- 偶数次采样取下中位数（偏保守，避免平均出非法等级）。

**三条规则：**

1. **不平均总分。** 等级是序数：某点只有 0/1 两级时，平均值 0.5 不是合法等级；而平均总分也不对应任何一次真实判定，导致"总分与逐条明细对不上"。
2. **先接地，再聚合。** 证据无法定位的判定按 0 分计入，不当作有效投票——否则编造的满分会被中位数采信。按 0 计入还会放大离散度，提高转人工概率。
3. **总分必须由聚合后的等级重算**，不能用聚合分反推明细。

> **采样有效性的前提**：同一提示词多次调用必须真的产生差异。上线前先做"同输入重复 5 次"的实测；
> 若输出完全一致，多采样只是花 N 倍钱买同一个答案，应改用其他手段（调整采分点排列顺序、放宽采样参数、换模型档位）。
> 思考型模型可能忽略 `temperature`，其随机性**必须实测，不能假设**。

### 2.2 自动通过 / 转人工

四项信号，任一不满足即转人工：

| 信号 | 条件 | 默认阈值 |
| --- | --- | --- |
| 接地率 | 等于 1.0 | — |
| 采样间总分极差 | 不超过 1 | `max_total_range` |
| 置信度 | 不低于 0.75 | `confidence_threshold` |
| 边界带 | 距满分 > 0.5，且距及格线（满分 60%）> 1 | `full_marks_margin` / `boundary_margin` |

**满分答案默认转人工**——有意为之：后果最重的判定多复核一次是合理代价。
多次采样一致是自动通过的**必要**条件，不是充分条件。

`risk` 只用于复核队列排序：`极差 + (1 − 置信度) × 满分 + 1`，量纲混加、无理论依据，
只在同一批次内可比，不得用于跨批次比较。

### 2.3 复核与定稿

`route == 'human'` 的结果进入待复核队列，按 `risk` 从高到低排。复核人看到的三样东西缺一不可：
逐条判定、证据在**原文**中的位置（`span_start` / `span_end`）、以及每次采样各自的等级——
最后一项才是"这个分数稳不稳"的直接证据，而不是只看一个总结论。

`risk` 只在同一批次内可比（§2.2），所以队列**先按题号分组、组内按 `risk` 排序**。
跨题混排再按 `risk` 排，等于把不可比的数字放在一起比较；要跨题处理时按题切换，
不要拿两个题的 `risk` 数值直接比大小。

三条不改写原则：

| 原则 | 理由 |
| --- | --- |
| 复核**追加** `reviews` 一行，不改 `aggregated_results` | 模型给的原分与人工定的最终分是两份证据。覆盖掉就再也说不清"改了多少、为什么改" |
| 一份结果只定稿一次（唯一索引兜底） | 定稿是结论，不是草稿。重复点击或并发提交由数据库挡住，返回 409 |
| 批量通过只接受 `route == 'auto'` | 否则"批量通过"就是绕过路由的后门：边界分、未接地、采样分歧会被一键放行 |

**"定稿后不因标准修改而变化"** 由两件事共同保证：结果落库后不重算（§5.5），
以及复核记录指向**某一个** `aggregate_id`。标准改了只会让下一次评分指向新快照，
已定稿的那一行仍读当时的 `rubrics.schema_json`。

**同一份答卷被重新评分时，旧结果不再进队列。** 队列只列"每份答卷当前最新的一次结论"，
接口也拒绝给旧结论定稿（409 `RESULT_SUPERSEDED`）——否则教师拿着旧页面的链接，
会把一份已经被取代的分数确认成正式成绩。详情页对这类旧结论返回 `current=false` 与
`superseded_by`，让页面能直接提示"此结果已被重评取代"，而不是靠教师自己看出来。

**定稿之后不允许重新评分。** `create_task` 拒绝已定稿的答卷，`store.save_evaluation_for_answer`
再挡一次（防止并发任务绕过入口）。没有这个约束，同一份答卷会出现两条互不隶属的"最终"记录——
那比"暂时不能重评"更糟。更正需要撤销定稿，当前版本没有该入口（§9 记为未决）。

## 3. 模块划分

```text
backend/
├── app/
│   ├── main.py               # FastAPI 装配：工厂函数 + 启动期口令校验
│   ├── config.py             # .env 读取（无第三方依赖）
│   ├── api/
│   │   ├── routes.py         # 登录 / 退出 / 会话 / 健康检查
│   │   ├── answers.py        # 答卷导入：预览与入库
│   │   ├── tasks.py          # 批量任务：创建 / 进度 / 重试
│   │   ├── results.py        # 复核：待复核队列 / 详情 / 改分 / 批量通过
│   │   ├── rubrics.py        # 标准：生成 / 带意见迭代 / 保存 / 列表 / 按版本打开
│   │   ├── deps.py           # 认证依赖（401）与数据库依赖（503）
│   │   ├── errors.py         # 领域异常 → 状态码；422 不回显请求内容
│   │   └── schemas.py        # 请求响应模型（pydantic）
│   ├── auth.py               # 共享口令校验 + 服务端会话登记（可撤销、可过期）
│   ├── importing.py          # 答卷导入解析（纯函数，不碰库与 HTTP）
│   ├── core/
│   │   ├── rubric.py         # 4.1 的校验与加载；问题分「阻断 / 仅提示」两类
│   │   ├── json_extract.py   # 模型输出 → JSON 对象（剥围栏、报错码）
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
│   ├── pipeline.py           # 编排：调用 → 解析 → 校验 → 服务端算分
│   ├── rubric_gen.py         # 编排：5.2 自然语言 → 结构化标准草稿（含一次重试与三视图）
│   ├── tasks.py              # 编排：批量评分任务的线程池执行器
│   └── store/                # 存储层（MySQL 8）
│       ├── tables.py         # 10 张表的定义；方言差异收在 with_variant 里
│       ├── repository.py     # 写入（一个事务）与读取（还原成领域对象）
│       └── __init__.py       # 对外暴露 Store、RunMeta/RunRecord、StoredEvaluation、
│                             #   ImportOutcome、QuestionNotFound（metadata 给迁移用）
├── migrations/               # Alembic 迁移脚本（表结构的部署真源）
├── alembic.ini
├── tests/                    # 纯函数单测 + 合成用例回归
└── scripts/                  # check（不调模型）/ run（真调模型）

frontend/                     # 管理端（Vue 3 + Vite + TypeScript + Element Plus）
├── src/
│   ├── main.ts               # 装配：Element Plus + 路由 + 全局错误兜底
│   ├── App.vue               # ErrorBoundary + router-view
│   ├── api/client.ts         # fetch 封装：统一 ApiError / 401 / 网络错误
│   ├── api/auth.ts           # 登录、退出、会话
│   ├── session.ts            # 登录态：/api/auth/session 只确认一次
│   ├── feedback.ts           # 统一错误提示（ElMessage）
│   ├── router/index.ts       # 路由与登录守卫
│   ├── components/           # ErrorBoundary（渲染错误兜底）
│   ├── layouts/              # AdminLayout
│   └── views/                # LoginView / HomeView / NotFoundView / LoadErrorView
├── tests/                    # Vitest + jsdom
└── vite.config.ts            # dev proxy /api → :8000；Vitest 配置
```

**依赖方向只能自上而下，禁止反向引用：**

| 层 | 模块 | 允许依赖 |
| --- | --- | --- |
| 入口 | `main.py`、`api/` | 以下全部 |
| 适配 | `auth.py` | 仅标准库（不读环境变量：口令由入口传进来） |
| 编排 | `importing.py` | 仅标准库（不碰库、不碰 HTTP） |
| 编排 | `pipeline.py` | core、providers |
| 编排 | `rubric_gen.py` | core、providers |
| 编排 | `tasks.py` | core、providers、store |
| 编排 | `store/repository.py` | core、providers，以及 `pipeline.Evaluation`（"一次评分的产出"这个类型由 pipeline 定义） |
| 领域逻辑 | `core/*` | 仅标准库 + pydantic |
| 适配 | `providers/*`（厂商 SDK）、`store/tables.py`（SQLAlchemy 方言） | 外部依赖 |
| 工具 | `scripts/*`、`tests/*` | 全部 |

`core/` 必须不 import 任何厂商 SDK，也不读环境变量——这是"可单测、可替换"的物理保证。
`store/` 是唯一允许依赖 SQLAlchemy 的地方；`pipeline.py` 只认 `Store` 的方法，不碰 SQL。
反过来，`pipeline` **不** import `store`：谁调谁由入口决定，编排层之间保持单向。

根目录的 `docker-compose.yml` 与 `scripts/dev-db.sh` 属于部署工具，不参与模块依赖。

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

### 4.4 存储表（MySQL 8，逻辑模型与库无关）

```text
questions(id, code, stem, type, total_score, status, created_at)
rubrics(id, question_id, version, schema_json, checksum, source_text, feedback,
        parent_rubric_id, created_at)
answers(id, question_id, student_pseudo_id, raw_text, normalized_text, content_hash, source, truncated, created_at)
grading_runs(id, answer_id, rubric_id, prompt_version, model, params_json, status,
             score, comment, tokens_in, tokens_out, latency_ms, error, created_at)
item_results(id, run_id, kind, rubric_item_id, level, hit, evidence,
             span_start, span_end, grounded, reason)
grading_tasks(id, question_id, rubric_id, samples, status, total, done, failed,
              error, created_at, started_at, finished_at)
task_items(id, task_id, answer_id, status, error, aggregate_id, updated_at)
aggregated_results(id, answer_id, rubric_id, sample_run_ids, levels_json, sample_totals_json,
                   raw_score, calibrated_score, max_score, dispersion, total_range,
                   confidence, grounding_rate, route, risk, reasons_json,
                   applied_deductions_json, calibration_version, created_at)
reviews(id, aggregated_result_id, reviewer_id, final_score, decision, comment, created_at)
calibration_models(id, version, method, params_json, train_size, metrics_json, created_at)
```

**几条必须写明的约定：**

| 约定 | 理由 |
| --- | --- |
| `questions.code` 存业务编号（`Q-01`），其余表用整型外键 | 人能读的编号与数据库主键分开，改编号不会牵动全库 |
| `grading_runs.rubric_id` 指向不可变快照，不再存 `rubric_version` | 版本号存两处必然漂移；外键指过去，版本号 join 出来 |
| `rubrics` 的内容只增不改，`checksum` 是 `schema_json` 的 sha256 | 内容没变就复用最新一版；变了追加一版。旧运行永远显示当时的标准。溯源列只补空值，不改写内容 |
| `rubrics.source_text` / `feedback` 记录这一版怎么来的 | 首次保存写教师原话，迭代写那条意见，手工编辑为空串。没有它们，"这个权重怎么来的"只能翻聊天记录（§5.6） |
| `rubrics.parent_rubric_id` 记录这一版改自哪一版 | 版本号只说明先后，不说明来源；改旧版再存时两者会不一致。自引用字段**不加外键**：标准只增不删，父版本不会消失，而 SQLite 加外键要重建整表 |
| 复用同内容版本时只**补空**的溯源 | 评分写入路径可能先建过同一内容、却没带原话；这次保存把空着的 `source_text`/`feedback` 补上，不覆盖已有记录 |
| 保存标准时同步 `questions.stem` / `total_score` | 标准里存一份、题目表里存一份（列表页要读）。只创建不更新会留下"标准说 12 分、题目说 10 分"两套真相 |
| `item_results.kind` 区分 `item` / `deduction`，`level` 可空 | 扣分项没有等级、只有命中与否，硬塞进同一个字段会造出"扣分项等级 0"这种假数据 |
| 分值统一 `DOUBLE` 且 `asdecimal=False` | 否则 MySQL 回来是 `Decimal`、SQLite 回来是 `float`，同一份结果两副面孔 |
| 所有表 `utf8mb4` + `InnoDB` | 唯一能完整存下 emoji 的字符集；中文不需要特殊照顾，不需要照顾的才不容易坏 |
| `span_start`/`span_end` 是**原文**偏移 | 高亮要落在学生原话上，规范化后的文本只作审计与检索 |
| `answers.content_hash` = sha256(规范化文本) | 查重要按内容比对，不能对 LONGTEXT 做等值查询；规范化后全角/半角、多空格都算同一份 |
| `answers.source` ∈ `csv` / `paste` | 答卷是批量导入还是单份补录，出问题时能区分 |
| `content_hash` 的空串是历史遗留 | 加这一列之前写入的行拿不到指纹（迁移不会去重算）。**代价：这些行查不出重复**，需要时跑一次回填脚本（尚未提供） |
| `calibrated_score` 与 `calibration_version` 校准上线前恒为 `NULL` | 字段先留，语义不含糊：`NULL` 就是"没校准"，不是"校准为 0" |
| `grading_tasks.rubric_id` 是**任务级快照** | 任务一建就固定标准，之后教师改标准不影响本批次（架构 §5.5） |
| `task_items` 不存"待复核" | 待复核 = `aggregated_results.route == 'human'`。存两份迟早出现"状态说已评、路由说待复核"的矛盾 |
| `task_items` 的 claim 用 CAS | 先读候选、再带 `status='pending'` 条件更新；两个 worker 抢同一行时只有一个 rowcount=1 |
| 计数用 `done = done + 1` 增量，不做"查出来再写回" | 两个 worker 同时完成时，覆盖式写入会丢掉一次 |
| `grading_tasks.error` 只记**任务级**失败 | 单份失败在 `task_items.error`；执行器崩溃 / 进程中断才写任务级 |
| 接口里的 `state` 是**派生**的 | 结论被重评取代 → `superseded`；有复核记录 → `finalized`；否则 `graded` 且 `route=human` → `needs_review`；其余为 `graded`。数据库不存它，但接口必须给出，否则前端要自己拼 `status`、`route`、`final_score`、`current` 四个字段 |
| `reviews` 每个 `aggregated_result_id` 只有一行（唯一索引） | "定稿"是一次性的，重复提交由数据库挡住；用唯一索引而非表约束，SQLite 上的迁移不必重建整表 |
| `reviews.final_score` 与 `aggregated_results.raw_score` 并存 | 前者是人工定稿，后者是模型当时算的；覆盖掉就丢了"改了多少"这个证据 |
| 复核队列只列每份答卷**最新一次**聚合结果（"已定稿"列表不套这个过滤） | 待办必须挂在当前结论上；历史是历史，不能被"最新一次"过滤掉 |
| 已定稿的答卷不能重新评分，也不能进入新任务 | 否则同一份答卷会出现两条互不隶属的定稿记录；更正需要显式的撤销流程 |
| 任务项带 `current`，过期结论派生为 `superseded` | 任务保留当时那一行（任务级快照），但同一答卷被别的任务重评后，本任务那行已经过期；不标出来，教师会对着一个无法定稿的旧分数找操作入口 |
| 批量通过只接受 `route = 'auto'` | 转人工的结果必须逐份看，否则批量通过成为绕过路由的后门 |

`student_pseudo_id` 是脱敏占位符，不存姓名学号。审计日志与申诉表留到有真实用户时再加。

**表结构怎么落地**

- 代码真源是 `backend/app/store/tables.py`；部署真源是 `backend/migrations/`（Alembic）。
- 两者由一条测试强制对齐：真的跑一遍 `upgrade head` / `downgrade base`，再把迁移出来的
  表名、列、可空性、索引、唯一约束、外键与 `tables.py` 逐项对照。只有这样，"测试库建得起来"
  才等于"生产库建得起来"。
- 一键建库：`./scripts/dev-db.sh up`（起 MySQL → 等就绪 → `alembic upgrade head`），
  连数据一起重来用 `reset`。

## 5. 教师端：评分标准怎么产生

教师不写 JSON，也不写 XML。流程是：**一段自然语言 → 一次模型调用 → 三视图确认 → 迭代或行内修改**。

### 5.1 输入

| 项 | 必填 | 说明 |
| --- | --- | --- |
| 题干 | 是 | 没有题干，模型不知道这段标准在评什么 |
| 满分 | 是 | 没有满分，权重无从分配 |
| 评分标准原文 | 是 | 教师原话，如"答对三个要点给 6 分……" |
| 参考答案 | 否 | 强烈建议填写，能显著改善等级划分质量 |
| 题号 | 否 | 默认 `Q-01`；多题场景在保存环节选择（#11） |

题型、采分点 id、分值分配都不让教师填——由模型给草稿。

### 5.2 生成

一次调用，使用**强档模型**（与评分模型分开配置：生成只跑一次，评分要跑 N×M 次）。
落地方式是两个环境变量：评分读 `DEEPSEEK_MODEL`，生成读 `DEEPSEEK_GEN_MODEL`（留空则退回前者）。

system 中的三条硬纪律：

1. **只做结构化，不得新增教师未提及的采分点**。缺就缺，由校验清单提示差额，绝不自作主张补点。
2. 每个采分点必须给出可判定的 `criterion`，禁止"论述充分""理解到位"这类无法验证的措辞。
3. 只输出 JSON，字段固定，不得输出总分或其他派生字段。

服务端校验：schema 合法性、id 唯一、`Σweight == total_score`、等级分值域、等级单调。
不合法 → 带上错误信息重试 1 次 → 仍失败则回显错误、不落库（**不静默保存半成品**）。

**迭代生成**（`POST /api/rubrics/{id}/iterate`）复用同一套校验与重试，只换提示词：
上一版**整份** JSON 与一条修改意见一起进 `<previous>` / `<feedback>` 标签，system 明确
"只改教师指出的部分，其余保持不变"。题号、题干、满分、参考答案与 `rules` 仍由服务端保留——
模型改了也会被丢弃。**上一版必须整份带上**，否则它会当成"重新生成"，教师确认过的权重与等级
会整个漂移（§5.4）。

### 5.3 确认（三视图，缺一不可）

| 视图 | 回答的问题 |
| --- | --- |
| 自然语言摘要 | 这份标准在考什么——**对不对** |
| 结构化预览表（id / 描述 / 权重 / 各等级与分值） | 有没有漏、有没有重复——**全不全** |
| 校验清单 | 权重和是否等于满分、等级是否都有判定依据——**合不合法** |

结构错误（权重和不等、id 重复、分值越界）**阻断保存**；语义问题（采分点疑似重叠、措辞不可判定、覆盖度不足）**只警告**，并允许教师标记"已知悉"。

校验结果在代码里是 (code, message, blocking) 三元组（`core/rubric.py` 的 `RubricIssue`），
`code` 稳定、`message` 给人看。当前实现的两类问题：

| 类别 | code | 判定 |
| --- | --- | --- |
| 阻断 | `WEIGHT_SUM` | 权重合计与满分不等；**消息里写明差额**（"还有 3 分未分配"），不自动补点 |
| 阻断 | `DUPLICATE_ID` / `SCORE_OUT_OF_RANGE` / `LEVEL_NOT_MONOTONIC` / `TOO_FEW_LEVELS` / `WEIGHT_NON_POSITIVE` | 标准自身不成立 |
| 阻断 | `PENALTY_POSITIVE` / `CAP_POSITIVE` / `CAP_INEFFECTIVE` | 扣分项定义不成立 |
| 阻断 | `MISSING_FIELD` / `BAD_TYPE` / `BAD_ITEMS` / `BAD_ITEM` / … | JSON 结构不对，连解析都不成立 |
| 仅提示 | `TOP_LEVEL_BELOW_WEIGHT` | 最高档分值低于权重，该点满分不可达（见 §9 待定项） |
| 仅提示 | `EMPTY_CRITERION` | 某档没有判定依据 |
| 仅提示 | `VAGUE_CRITERION` | 判定依据含"论述充分"这类不可验证措辞（固定词表，属启发式） |
| 仅提示 | `DUPLICATE_DESC` | 两个采分点描述完全相同，疑似重复 |
| **未实现** | — | 「采分点疑似重叠（近似）」「覆盖度不足」需要语义比较，当前不做；只做了"描述完全相同"这个确定性子集。宁可漏报，也不要用不可靠的相似度去吓人 |

**权重不足时不补点**是硬规则：模型与服务端都不许替教师编采分点，只报差额。
这条与"模型不得新增教师未提及的采分点"是同一件事的两面。

> 只给自然语言摘要等于让教师盲签：摘要永远看着合理，而结构错误它不会暴露。

### 5.4 修改

两条路并存，缺一不可：

- **迭代生成**：带上上一版 JSON + 教师的修改意见（"P3 拆得太粗"），只改指定部分。
  接口是 `POST /api/rubrics/{id}/iterate`。
- **行内编辑**：直接在预览表上改某一行（权重、描述、等级分值）。
  接口是 `PUT /api/rubrics/{id}`：整份提交，服务端校验后**追加一版**，旧版原样保留。

迭代响应附带服务端算出的**变更清单**（哪些采分点新增 / 删除 / 改了哪个字段）。模型能不能
"只改被指出的部分"无法由代码强制，但差异必须当场可见——否则教师得逐行比对两份标准，
才能发现模型顺手动了别处。

迭代得到的是**草稿**，保存时用 `PUT /api/rubrics/{based_on.rubric_id}`（而不是 `POST`），
新版本的 `parent_rubric_id` 才会指向上一版；否则版本号只说明先后，说明不了来源。

"彻底重来"作为独立按钮保留，但默认不用。**如果只能靠重新生成，教师会被模型绑架**——改一句话，整份结构全变，之前确认过的部分也没了。

### 5.5 生效与快照

**不做版本表，一切以最新版为准。** 但有两处快照必须存，否则"不建版本表"会破坏可追责性：

1. **任务级快照**：评分任务启动时把当前 rubric 快照进该任务，保证一个批次内所有答卷用同一份标准。
2. **记录级快照**：每条评分记录冗余存 rubric 内容（或 `checksum + schema_json`），保证日后 rubric 被改、被拆、被复用 id 时，历史明细仍能正确显示当时考的是什么。

"改标准不影响历史成绩"由**结果落库后不重算 + 明细自带快照**保证，而不是靠版本管理。

### 5.6 留档

教师原话与历次修改意见都存，作为"采分点为什么这么拆"的来源证据。答辩被问"这个权重怎么来的"，要能翻出原文。

落地为 `rubrics` 的三列：`source_text`（这一版依据的教师原话）、`feedback`（生成这一版时
的修改意见）与 `parent_rubric_id`（改自哪一版）。它们跟着版本走——同一道题的每一版都有自己的
来源，而不是只在题目上记一份。

**首次保存强制要求原话**（`POST /api/rubrics` 缺 `source_text` 返回 400 `SOURCE_TEXT_REQUIRED`）；
**行内编辑**没重发原话时沿用上一版的 `source_text`，不会因为"改一行"把教师原话抹掉。
若同一内容早已被评分路径写入（没有原话），这次保存会把空着的溯源补上，但**不覆盖**已有记录。

### 5.7 答卷从哪来

**不做学生端。** 答卷由教师导入，两种方式：

| 方式 | 场景 | 说明 |
| --- | --- | --- |
| 批量导入 | 整个班 | Excel / CSV 或粘贴：一行一份，列为「学号占位 + 题号 + 答案」；导入前预览、查重 |
| 单份录入 | 试评、演示、补录 | 直接粘贴一段答案 |

约束：

- **不支持图片答卷**（拍照/扫描）。本期只处理文本；`source` 字段预留，未来接 OCR 时填。
- 学生不登录、不接触系统，因此不需要学生账号表、token 或 session UUID。
- 学号只作为导入文件里的占位标识，落库前脱敏为 `student_pseudo_id`（如 `S-0001`），**不存真实学号与姓名**。

**导入规则（`app/importing.py`，纯函数）**

| 项 | 规则 |
| --- | --- |
| 两种来源 | `csv`：一行一份；`paste`：整段文本算一份答案，题号由调用方给出 |
| 列数 | 无题号参数时 2 列（题号,答案）或 3 列（学号,题号,答案）；给了题号时 1 列（答案）或 2 列（学号,答案） |
| 表头 | 可选。**所有**非空单元格都是表头词才认定为表头（否则数据行会被误当表头） |
| 列数一致性 | 以第一行定基准宽度；后续行列数不同 → 该行进坏行清单，不影响其他行 |
| 坏行 | 只记录**行号 + 原因**，**绝不回显原文**——坏行里哪一列是学号无从判断，回显等于把学号送到前端与日志 |
| 查重 | 同题号 + `content_hash` 相同即视为重复；批内重复与库内重复都算 |
| 脱敏编号 | 按题号各自连续编号 `S-0001`、`S-0002`…，与真实学号无关；学号列读到即丢 |
| 预览与入库 | 两条路由用**同一段原文重新解析**，不接收客户端回传的行——否则预览与入库会悄悄对不上 |
| 取消 | 只调预览、不调入库，因此不产生任何数据；入库本身是单事务 |

> 预览里 `rows_with_student_id` 的含义是"成功解析且带学号的行数"，坏行不计入。

这一决定砍掉了整个学生端：少一个角色、少一套权限、少一类页面。演示时只需一个窗口。

### 5.8 认证与访问控制

单用户系统，不做账号体系：

- 管理端由**一个共享口令**保护，口令来自环境变量 `ADMIN_PASSWORD`，不进数据库、不进代码。
- 校验通过后下发 **HttpOnly cookie**，有效期可配置（`ADMIN_SESSION_HOURS`，默认 8 小时）；退出即失效。
- 口令用 `hmac.compare_digest` 比较，不因"前几位对了"而提前返回。口令错误时响应只有一句
  "口令错误"——不区分"口令为空""口令不对""没有这个用户"（本系统也没有用户）。
- 服务启动时口令为空就**直接失败**，不存在"没配口令也能跑"的默认路径。
- 学生不接触系统（答卷由教师导入），因此没有学生账号、token 或 session。
- 公网部署必须启用 HTTPS，否则口令在传输中明文可见。

**会话为什么必须有服务端状态（与最初设想不同的地方）**

最初写的是"签名 cookie"，即无状态令牌。但验收要求"退出后原凭据立即不可用"——
签名只能证明"这个 cookie 是我发的"，不能证明"它还没被撤销"：退出后旧 cookie 仍能通过校验。
所以实现改成**随机会话 id + 服务端登记表**：登录时登记一行，退出时删掉，重放旧 cookie 立刻 401。

代价必须写清楚：

| 代价 | 现状 | 升级路径 |
| --- | --- | --- |
| 重启即全部会话失效 | 内存字典，单进程 | 换成共享存储（Redis / 一张 `admin_sessions` 表），`AdminAuth` 的接口不用改 |
| 多 worker 之间不共享 | 同上 | 同上 |
| 没有登录频率限制 | 未实现 | 加失败计数与退避；单用户场景优先级低 |

> 共享口令无法区分"是谁操作的"。单用户场景可以接受；一旦需要多人协作，再升级为账号体系，并补审计日志。

### 5.9 管理端骨架

前端只做三件事：**登录、路由、把错误说清楚**。业务页留到 #13–#15。

| 决定 | 理由 | 代价 |
| --- | --- | --- |
| 登录态只放在内存 + 服务端 cookie | 不在 localStorage 里存 token/`authenticated` 标记；刷新时重放 `GET /api/auth/session`，以服务端为准 | 每次刷新多一次请求（可忽略） |
| 所有非公开路由先过守卫 | 未登录访问未知路径也先去登录页，不能因为"404"就露出管理端骨架 | 404 页也要求登录 |
| 401 立即回登录页 | `api/client` 发现 401 就清本地登录态，并调用路由层注册的导航回调；不是等"下一次导航" | 无法在页面上区分"过期"与"从没登录" |
| 错误文案只有一个映射 | `ApiError` + `errorMessage` 统一生成可读句子；登录失败贴在表单旁，其余错误用全局提示 | 同一条错误不会两处同时出现，但需要页面自己选呈现方式 |
| 两级渲染兜底 | `AdminLayout` 内层 `ErrorBoundary` 只替换内容区（导航还在）；`App` 外层兜住登录页等其余渲染错误 | 内层边界随路由 remount，会丢掉出错页面的本地状态 |
| 懒加载失败有回退页 | `router.onError` + 主包里的 `/load-error`：chunk 加载失败不会只留白屏 | 回退页只提供"刷新"，不能自动重试 |
| 开发期用 Vite 代理 `/api` | 前后端同源，cookie 与 CORS 都不用额外配置 | 生产部署必须让静态站点与 `/api` 同源（反代） |

**测试边界**：Vitest 只断言"行为"——守卫去了哪个路由、请求发了什么、错误提示是不是可读；
不断言样式与 Element Plus 内部实现。

## 6. 关键设计决策

| # | 决策点 | 备选 | 选择 | 代价（必须承认） |
| --- | --- | --- | --- | --- |
| D1 | 评分单位 | 整体总分 / 按采分点 | **采分点** | 评分标准必须先人工结构化，成本前移 |
| D2 | 谁算分数 | 模型输出 / 服务端加权 | **服务端** | 需维护等级-分值映射，灵活性下降 |
| D3 | 可信性来源 | 让模型写理由 / 校验证据可定位 | **可定位证据** | 可能误杀正确判定；短证据定位不稳 |
| D4 | 稳定性来源 | 调参求确定 / 多次采样 | **采样聚合** | N 倍成本与延迟；只压随机误差 |
| D5 | 自动化边界 | 全自动 / 置信度路由 | **路由** | 需人工池与阈值校准，且要证明没牺牲质量 |
| D6 | 教师如何录入标准 | 手写 JSON / 逐项表单 / 自然语言生成 | **自然语言 → 草稿 → 三视图确认** | 模型可能理解偏差，全靠确认环节兜住 |
| D7 | 标准的版本策略 | 版本表 / 最新版 | **最新版 + 两处快照** | 无法回滚到旧标准；靠快照保住历史可读 |

**与成熟实现的关系**：开源项目 LLM Grader（NYU 课程在用）同样把分数计算放在服务端、由模型只出逐条判定。
这印证 D2 不是臆断。但它的目标是课堂即时反馈，**没有**采样聚合、证据定位校验、置信度与人工路由——
这四项是本系统的增量，也是第 8 节要验证的对象。

## 7. 失败与边界

| 情况 | 处理 | 用户可见性 |
| --- | --- | --- |
| 模型返回空内容 | 指数退避重试 ≤3 次 | 仍失败 → 标记人工复核 |
| JSON 不合法 / 缺采分点 | 重试；仍缺 → `INCOMPLETE` | 复核界面标红 |
| 等级不在允许集合 | 该条作废，按 0 分计 | 记录 `INVALID_LEVEL` |
| 证据无法定位 | 该点判 0 并记 `UNGROUNDED`（保留模型的命中声称用于审计） | 复核界面显示"证据未找到" |
| 答案超长被截断 | 分段评分后合并，或首尾保留 | 记录 `truncated=true` |
| 答案含注入指令 | 分隔符包裹 + system 声明 + 记录 | 抽检队列 |
| 429 / 5xx / 超时 | 重试（含抖动） | 状态列可见 |
| 400 / 401 / 402 | 不重试，直接失败 | 明确错误码，不吞异常 |

## 8. 验证方式

### 8.1 没有真实答卷时（当前阶段）

用**合成用例**先立回归：每条用例 = 答案文本 + 期望总分 + 每条采分点的期望等级。

- `check`：**不调模型**，只校验用例与 rubric 的一致性（qtag 是否存在、id 是否失效、期望值是否超出取值域）。
- `run`：真调模型，比对期望。**每套 rubric 至少有一条"正确答案"用例**——否则容易写出"能抓所有错、也判错正确"的规则。

这一步让"没有数据"不再是阻塞项：语料可以后补，回归测试先跑起来。

### 8.2 有标注数据后

| 对照组 | 配置 | 回答的问题 |
| --- | --- | --- |
| B0 | 一段提示词直接要总分 | 系统比朴素做法好在哪 |
| B2 | 逐点判定、无证据校验 | 结构化本身带来多少 |
| B3 | B2 + 证据接地 | 接地是否真的拦住了编造 |
| B5 | B3 + 采样 + 校准 + 路由 | 完整系统的上限 |

指标：QWK（主）、MAE、ICC(2,1)、grounding rate、自动通过率与通过样本上的人机一致率。
**必须同时报教师之间的一致性作为参照系**，否则"准确率 0.8"没有意义。

### 8.3 结论的边界（不许夸大）

- 接地只证明"引用了原文"，不证明"判定正确"。
- 多采样只压随机误差，压不了系统性偏差。
- 提示注入只能缓解，不能消除。
- B0 的对比是设计合理性的**唯一**证据，不能省。

## 9. 待定项

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 语言 / 框架 | **已定** Python 3.10+ / FastAPI | 只承担 API 层 |
| 服务形态 | **已定** 单体 | 课题规模不需要拆服务 |
| LLM 供应商与参数 | **已接入** DeepSeek | `providers/deepseek.py`，标准库实现；模型、超时、思考强度均可配 |
| 存储 | **已实现** MySQL 8 | `utf8mb4` / InnoDB；表结构见 4.4，逻辑模型与库无关 |
| 迁移工具 | **已定** Alembic | 表结构代码真源 `store/tables.py`，部署真源 `migrations/`，由测试强制对齐 |
| 数据库访问 | **已定** SQLAlchemy 2.0 Core + PyMySQL | 只有 `store/` 依赖它；`core/` 与 `pipeline.py` 不碰 SQL |
| 任务执行 | **已实现** 进程内线程池 | `GRADING_CONCURRENCY`（默认 2，上限 4）。重启时把 running 任务标为 interrupted 并可重试；多 worker 部署需换共享队列 |
| 前端 | **已实现骨架** Vue 3 + Vite + TypeScript | Element Plus；Vitest + jsdom；业务页在 #13–#15 |
| 认证 | **已实现** 单口令 + 会话 cookie | 口令存 `ADMIN_PASSWORD`，缺失则启动失败；会话服务端登记，退出即可撤销。会话存内存（重启失效），多进程需换共享存储 |
| 接口层 | **已实现** FastAPI | 认证、答卷导入、批量任务、复核（队列/详情/改分/批量通过）、评分标准（生成/迭代/保存/列表/按版本打开）；管理端骨架已就绪（#12），业务页待 #13–#15 |
| 部署 | 待定 | 本地演示优先 |
| 最高档分值是否必须等于权重 | 待定 | 目前只作为 `TOP_LEVEL_BELOW_WEIGHT` **提示**，不拦保存。加硬校验会让"按权重算分"名副其实，但会约束评分标准的写法 |

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
