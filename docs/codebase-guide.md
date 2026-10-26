# 代码库导读指南

> **这份文档是"怎么读"，不是"是什么"。**
> 设计真相只有一份：`docs/architecture.md`。本文件只负责把你带到那些代码面前，
> 告诉你**按什么顺序读、每一层要看出什么、哪些地方最容易读错**。
>
> 若两者冲突，以 `docs/architecture.md` 为准；发现冲突请改本文件。

**当前状态**：75 个测试通过（3 个 MySQL 真库用例默认跳过），约 5100 行（含文档与测试）。
已完成的工单：#2 单份评分闭环、#3 证据接地、#4 多采样与路由、#5 持久化。
**没有**的部分见 §8，别在代码里找不存在的东西。

---

## 0. 一分钟鸟瞰

### 这个系统在做什么

> 把"让模型给总分"改造成"让模型**逐条判定采分点、并给出原文证据**"，
> 分数由服务端按权重算；再用多次采样的一致性当置信度，低的转人工。

一句话记住分工：

| 谁 | 负责 | 不负责 |
| --- | --- | --- |
| 大模型 | 每个采分点"达到哪一级" + 引用原文哪一句 | **不算分、不给总分** |
| 服务端 | 校验、算分、聚合、置信度、路由 | 不判断"答得对不对"（那要靠采分点定义） |

### 代码怎么分布

```text
backend/
├── app/
│   ├── core/            ← 纯逻辑，只用标准库，可离线单测（含 types / normalize / grounding /
│   │                        rubric / prompt / aggregate / routing）
│   ├── providers/       ← 模型适配层（唯一碰网络的地方）
│   ├── pipeline.py      ← 编排：把上面两层串成"一次评分"
│   ├── store/           ← 持久化（唯一碰 SQL 的地方）
│   ├── cli.py           ← 命令行入口与报告组装
│   └── config.py        ← .env 读取
├── migrations/          ← Alembic 迁移（表结构的部署真源）
├── tests/               ← 75 个用例
└── scripts/             ← 实验脚本（backend/scripts，如方差实测）

仓库根目录还有：docker-compose.yml（本地 MySQL 8）、scripts/dev-db.sh（一键起库 + 迁移）。
```

| 层 | 行数（约） | 依赖 | 读懂顺序 |
| --- | --- | --- | --- |
| `core/types.py` | 151 | 仅标准库 | ① |
| `core/normalize.py` + `core/grounding.py` | 132 | 仅标准库 | ② |
| `core/rubric.py` | 227 | 仅标准库 | ③ |
| `core/prompt.py` | 81 | 仅标准库 | ④ |
| `providers/` | 174 | 标准库 `urllib`（不装 SDK） | ⑤ |
| `pipeline.py` | 154 | core + providers | ⑥ |
| `core/aggregate.py` + `core/routing.py` | 194 | 仅标准库 | ⑦ |
| `store/` | 719 | SQLAlchemy | ⑧ |
| `cli.py` | 206 | 全部 | ⑨ |

**依赖方向是硬约束，不是风格建议。** 可以用一条命令自己验：

```bash
cd backend && grep -h "^import \|^from " app/core/*.py | grep -v "^from \." | sort -u
# 只会看到 dataclasses / difflib / json / pathlib / typing / unicodedata（+ __future__）
# 出现 requests、openai、sqlalchemy、pydantic 就是越界了
```

---

## 1. 八步数据流 → 文件对照表

架构 §2 定义了八步。这张表是"读到哪一步该打开哪个文件"。

| # | 步骤 | 代码位置 | 确定性 |
| --- | --- | --- | --- |
| 1 | 载入并校验评分标准 | `core/rubric.py:57` `from_dict` → `:125` `validate` | ✅ |
| 2 | 预处理答卷（规范化 + 偏移映射） | `core/normalize.py:12` `normalize_with_map` | ✅ |
| 3 | 组装提示词 | `core/prompt.py:59` `build_messages` | ✅ |
| 4 | **调用模型** | `providers/deepseek.py:104` `complete` | ❌ 唯一不可控的一步 |
| 5 | 解析 + 证据接地 | `pipeline.py:35` `extract_payload`、`:58` `_parse_items`、`core/grounding.py:92` `apply_grounding` | ✅ |
| 6 | 多采样聚合 | `core/aggregate.py:67` `aggregate` | ✅ |
| 7 | 服务端算分 | `core/aggregate.py:39` `sample_score`（**唯一算分入口**） | ✅ |
| 8 | 路由 + 落库 | `core/routing.py:34` `decide`、`store/repository.py:162` `save_evaluation` | ✅ |

**读这张表要抓住的一件事**：八步里只有第 4 步不可控，而它被 `GradingProvider` 一个方法包住。
其余七步全是纯函数或纯数据库操作。这就是为什么测试能覆盖 95% 而不联网：
`tests/fixtures.py` 里的 `CannedProvider` 替换掉第 4 步即可。

---

## 2. 逐层精读

每一节的结构都一样：**职责 → 打开哪个文件 → 要看出什么 → 动手验证**。

### ① 领域对象：`core/types.py`（约 15 分钟）

**职责**：定义"一套评分标准"和"一份判定结果"长什么样。**整个项目的数据词汇表**。

| 类 | 行 | 一句话 |
| --- | --- | --- |
| `Level` | 18 | 某个采分点的一档：等级标识 + 该档分值 |
| `RubricItem` | 27 | 一个采分点：权重 + 若干等级 |
| `Deduction` | 49 | 扣分项：`penalty` 与 `cap` 都是负数 |
| `Rubric` | 62 | 一整份评分标准 |
| `ItemJudgement` | 79 | 模型对某采分点的一次判定（含证据与 `span`） |
| `DeductionJudgement` | 101 | 对扣分项的一次判定 |
| `SampleJudgement` | 121 | **一次采样**的完整结果 |

**要看出什么**：

1. `RubricItem.score_for(level)`（`:39`）是"等级 → 分值"的唯一换算入口。
   别在别处再写一份 `if level == 1.0: ...`。
2. `ItemJudgement.mark_ungrounded()`（`:92`）**不只是**标记 `grounded=False`，
   它还会把 `level` 置 0。这是第 5 步"定位失败按 0 分计"的实现位置。
   同时 `hit` 保持模型的原话——"模型声称过什么"是审计信息，不能被覆盖。
3. `SampleJudgement.grounding_rate()`（`:134`）的分母**只算模型声称命中的项**。
   模型什么都没命中时返回 1.0，而不是 0.0。

**动手验证**：

```bash
cd backend
python3 -c "
from app.core.rubric import from_dict
from tests.fixtures import RUBRIC_DATA
r = from_dict(RUBRIC_DATA)
print(r.item('P2').allowed_levels, r.item('P2').score_for(0.5))
"
```

**读完要能回答**：为什么 `Level` 既有 `level` 又有 `score`？（因为等级是序数，分值是线性量；
聚合要在等级上做中位数，算分要在分值上做加权——混成一个字段就两头都做不好。）

### ② 证据接地：`core/normalize.py` + `core/grounding.py`（约 30 分钟）

**职责**：模型引用了"原文哪一句"，得真的能在答卷里找到；找不到就判 0。

**这是整个项目最有设计含量的一段代码**，也是答辩最容易被追问的地方。

读的顺序：`normalize_with_map`（27 行，先看）→ `locate_evidence`（85 行，主要难度）→ `apply_grounding`（14 行）。

三个必须看懂的关键点：

| 位置 | 在干什么 | 为什么这样做 |
| --- | --- | --- |
| `normalize.py:12` | 规范化的同时记一份"规范化下标 → 原文下标"的映射表 | 比对要忽略空格、全半角、大小写；但高亮必须落回**原文**第几个字。只要一处丢了映射，"高亮偏移一个字"这种 bug 就永远修不完 |
| `grounding.py:52-55` | 容错匹配前，先以"最长匹配块"为中心开一个**局部窗口** | 早期版本在全文范围做模糊匹配，结果模型随手引用的几个词被拼成一段 93 个字符的"命中"。局部窗口杜绝了这种"跨整卷拼凑" |
| `grounding.py:69` | `max_errors = max(1, int(length * (1 - 0.9)))` | 短证据（比如 4 个字）按比例算容错是 0 个字符，太苛刻；所以至少给 1 个字符的额度 |

**`_ground`（`:80`）里有一个容易读漏的分支**：

```python
if not judgement.hit:
    judgement.grounded = True   # 没声称命中 → 没什么可编造 → 视为接地
    return
```

这不是偷懒：`grounding_rate` 的分母是"声称命中的判定"，这条分支保证不把"什么都没提取到"
算成"编造证据"。

**动手验证**（自己感受模糊匹配的边界）：

```bash
cd backend
python3 -c "
from app.core.grounding import locate_evidence
ans = '客户端首先发送 SYN 报文，seq=x；服务器回复 SYN=1、ACK=1。'
print('精确：', locate_evidence(ans, 'SYN=1、ACK=1'))
print('容错：', locate_evidence(ans, 'SYN=1, ACK=1'))   # 全角顿号 vs 半角逗号
print('编造：', locate_evidence(ans, '四次挥手关闭连接'))
"
```

**读完要能回答**：接地成功证明了什么？（只证明"这句话真的在你卷子里"，
**不证明"判定是对的"**。这条边界必须能脱口而出，答辩会问。）

### ③ 标准校验：`core/rubric.py`（约 20 分钟）

**职责**：把 JSON 变成 `Rubric`，并**一次性报出所有问题**。

| 位置 | 在干什么 |
| --- | --- |
| `:16` `RubricError` | 带 `problems: list[str]`，不是单条消息 |
| `:57` `from_dict` | **收集式**解析：缺字段、类型错、语义错一起攒，最后统一抛 |
| `:63` `required` | 局部闭包，省掉每行 `try/except` |
| `:115-117` | 结构问题存在时，把"权重合计对不上"这条噪音过滤掉 |
| `:125` `validate` | 语义规则：权重和、id 唯一、等级单调、分值域、扣分封顶 |
| `:188` `to_dict` | **与 `from_dict` 严格互逆**，供入库快照与前端展示 |

**这是本项目"报错也要设计"的样板**：用户填错标准时，最烦的是一改一报错。
所以解析器绝不早退，而是把能查的全查完。

**`to_dict` 为什么必须严格互逆**："标准快照"这四个字的全部意义在于
存进去的和读出来的是同一份标准。`tests/test_rubric.py:84` 有个用例专门守这条。

**动手验证**：

```bash
cd backend
python3 -c "
import copy, json
from app.core.rubric import from_dict, RubricError
from tests.fixtures import RUBRIC_DATA
bad = copy.deepcopy(RUBRIC_DATA)
bad['total_score'] = 11                 # 权重和不对
bad['items'][1]['id'] = 'P1'            # id 重复
bad['items'][0]['levels'][0]['score'] = 5.0   # 超出权重
try: from_dict(bad)
except RubricError as e: print('\n'.join(e.problems))
"
# 期望：一次看到三条问题，而不是只报第一条
```

### ④ 提示词组装：`core/prompt.py`（约 15 分钟）

**职责**：把 rubric + 答卷拼成消息列表。**只有 81 行，但每一行都是纪律**。

- `SYSTEM`（文件开头）：六条纪律——不许算分、必须逐点、必须引用原文、不许编造、`level` 只能取给定值、证据不足按最低档。
- `OUTPUT_SHAPE`：模型必须输出的 JSON 形状。
- `:34` `render_rubric`：把 rubric 渲染成模型能读的文本。
- `:59` `build_messages`：**稳定前缀在前，答案在最后**。

**要看出什么**：顺序不是随意的。题目与采分点构成稳定前缀 → 命中提示词缓存（省钱）；
答卷作为最后一个可变块 → 同一套标准评多份卷时前缀完全一致。

**动手验证**：

```bash
cd backend
python3 -c "
from app.core.prompt import build_messages
from app.core.rubric import from_dict
from tests.fixtures import RUBRIC_DATA, ANSWER
msgs = build_messages(from_dict(RUBRIC_DATA), ANSWER)
print([m['role'] for m in msgs])
print('---最后一条的结尾---')
print(msgs[-1]['content'][-120:])
"
```

### ⑤ 模型适配：`providers/`（约 20 分钟）

**职责**：把"调用某个模型"收敛成一个方法：`complete(messages, sample_index) -> str`。

| 文件 | 关键点 |
| --- | --- |
| `base.py:25` `GradingProvider` | 一个 `Protocol`，只有 `complete` 一个方法 |
| `base.py:11` `ProviderError` | 带 `code` 与 `retryable`。`AUTH`/`NO_BALANCE`/`BAD_REQUEST` 不可重试，`RATE_LIMIT`/`SERVER`/`NETWORK`/`EMPTY_RESPONSE` 可重试 |
| `fake.py:6` `CannedProvider` | 按脚本返回字符串。**所有不联网的测试都靠它** |
| `deepseek.py:55` `_http_post` | 标准库 `urllib` 直接发 POST，没装任何 SDK |
| `deepseek.py:71` `error_for_status` | HTTP 状态码 → 领域错误码 |
| `deepseek.py:90` `build_body` | 请求体组装；**思考模式下不发 `temperature`** |
| `deepseek.py:104` `complete` | 发起请求 + 解析响应 + 空响应判定 |

**要看出什么**：

1. **`transport` 可注入**（`:86`）。`DeepSeekProvider(config, transport=fake_transport)` 让测试能覆盖
   请求形状、错误映射、空响应三条路径，全程不联网。`tests/test_deepseek_provider.py` 就是这么做的。
2. **不发 `temperature`** 是刻意的：思考模式会忽略它。"传了但无效"比"不传"更容易骗过自己。
3. 这里**没有任何评分逻辑**。它只负责"把消息发出去、把字符串拿回来"。

### ⑥ 编排：`pipeline.py`（约 30 分钟）

**职责**：把 1→5 步串起来，产出 `SampleJudgement`；再重复 n 次，产出 `Evaluation`。

读的顺序：`extract_payload`（`:35`）→ `_parse_items`（`:58`）→ `grade`（`:105`）→ `evaluate`（`:137`）。

| 位置 | 在干什么 | 读的时候注意 |
| --- | --- | --- |
| `:20` `_FENCE` | 剥掉 ```` ```json ```` 代码围栏 | 模型经常不听话地包一层 |
| `:23` `GradingError` | 带错误码：`INVALID_JSON` / `NOT_OBJECT` / `MISSING_ITEM` / `INVALID_LEVEL` | 这四种都是"输出不可用"，不是"得了 0 分" |
| `:58` `_parse_items` | 逐个采分点找模型给的条目 | 漏了任何一个就抛 `MISSING_ITEM`，不静默补 0 |
| `:105` `grade` | 调 provider → 解析 → 接地 → **用 `sample_score` 算分** | 注意它完全不读模型给的分数 |
| `:131` `Evaluation` | `samples + aggregate + route` 三件套 | 后面 store 的返回类型就是它 |

**这一节先把 `aggregate` 当黑盒**：`evaluate` 只是"跑 n 次 `grade`，再把 n 份结果交给
`aggregate`，最后问 `decide` 该不该转人工"。黑盒里发生了什么，下一节 ⑦ 再拆。

**要看出什么**：`grade` 里没有一处 `if score:` 或 `float(payload['total'])`。
模型输出里的 `score` / `max_points` 字段即使给了也**不会被读**。
`tests/test_pipeline.py:69` 专门喂了 `score=99, max_points=99` 验证这点。

**动手验证**：

```bash
cd backend
python3 -c "
import json
from app.core.rubric import from_dict
from app.pipeline import grade
from app.providers.fake import CannedProvider
from tests.fixtures import RUBRIC_DATA, ANSWER, SAMPLE_PAYLOAD
data = dict(SAMPLE_PAYLOAD); data['score'] = 99; data['max_points'] = 99
r = grade(from_dict(RUBRIC_DATA), ANSWER, CannedProvider([json.dumps(data, ensure_ascii=False)]))
print('模型说 99，服务端算出：', r.score)
"
# 期望 8.0
```

### ⑦ 聚合与路由：`core/aggregate.py` + `core/routing.py`（约 40 分钟）

**这是全项目最需要动脑的一层**，也是最容易理解错的一层。

`aggregate.py` 只有 5 个函数，但每个都对应一条规则：

| 位置 | 规则 | 为什么 |
| --- | --- | --- |
| `:30` `_median_level` | 取下中位数；若落在非法等级上，贴到最近的合法等级 | 偶数次采样求平均会算出 `0.5`，而某点可能只有 `0/1` 两档——平均值不是合法等级 |
| `:39` `sample_score` | 单次采样的分数：只统计**已接地**的扣分项 | 没定位到的扣分不算数 |
| `:55` `_deduction_is_majority` | 扣分项要多数采样都命中才生效 | 单次翻转不该影响最终分 |
| `:67` `aggregate` | 逐点中位数 → 用中位数等级**重算**总分 → 算离散度、置信度 | 见下 |

**三个一定要弄清的地方**：

1. **总分不是平均出来的。**
   `aggregate` 先对每个采分点取中位数等级，再用这批等级重算总分（`:81`）。
   三次采样的总分可能是 `8.0 / 6.5 / 6.0`，但聚合分是 `8.0` 而不是 `6.83`。
   理由：平均总分不对应任何一次真实判定，会导致"总分与逐条明细对不上"。

2. **离散度是加权的，扣分项也算进去。**
   `:100` 的 `spread_terms` 先按采分点权重加权，再把扣分项的翻转按扣分幅度折进去（`:101-111`），
   最后截断到 1。**只统计采分点会漏掉"扣分项反复翻转"这种分歧**——这是 #4 修掉的一个真 bug。

3. **置信度 = (1 − 离散度) × 接地率**（`:121`）。
   漏掉接地率这一项会怎样？三次全部编造证据时置信度仍是 1.0，直接自动通过。
   这是 #4 修掉的另一个 bug，`tests/test_aggregate.py:157` 守着它。

`routing.py` 只有 68 行，四项信号任一不满足就转人工（`:34`）：

```python
if aggregate.grounding_rate < 1.0: ...                    # 有未接地
if aggregate.total_range > policy.max_total_range: ...     # 采样间极差
if aggregate.confidence < policy.confidence_threshold: ...  # 置信度
if abs(score - max_score) <= full_marks_margin: ...        # 满分边界
if abs(score - pass_line) <= boundary_margin: ...          # 及格线边界
```

**满分答案默认转人工，是有意为之**：后果最重的判定多复核一次值得。
"三次一致就自动通过"是必要条件，不是充分条件。

**动手验证**（自己制造分歧，看指标怎么变）：

```bash
cd backend
python3 - <<'PY'
import json
from app.core.rubric import from_dict
from app.pipeline import evaluate
from app.providers.fake import CannedProvider
from tests.fixtures import RUBRIC_DATA, ANSWER, SAMPLE_PAYLOAD

def with_p1(level):
    d = json.loads(json.dumps(SAMPLE_PAYLOAD)); d['items'][0]['level'] = level
    return json.dumps(d, ensure_ascii=False)

r = from_dict(RUBRIC_DATA)
ev = evaluate(r, ANSWER, CannedProvider([with_p1(1.0), with_p1(0.5), with_p1(0.5)]), samples=3)
a = ev.aggregate
print('P1 中位数', a.levels['P1'], '| 分', a.score, '| 离散度', a.dispersion,
      '| 置信度', a.confidence, '| 极差', a.total_range)
print('路由', ev.route.route, ev.route.reasons)
PY
```

### ⑧ 持久化：`store/`（约 40 分钟）

**职责**：一次评分的全部数据落库，并按答卷号或运行号读回**领域对象本身**。

读的顺序：`tables.py`（8 张表，先看结构）→ `repository.py:162` `save_evaluation` → `:331` `load_evaluation`。

**`tables.py` 里值得单独看的几处**：

| 位置 | 内容 | 为什么 |
| --- | --- | --- |
| `:33` `MYSQL_TABLE_ARGS` | 所有表 `utf8mb4` + `InnoDB` | 唯一能完整存 emoji 的字符集 |
| `:40` `LongText` | `Text` 在 MySQL 上变成 `LONGTEXT` | MySQL 的 `TEXT` 只有 64KB，中文答卷会超 |
| `:43` `Score` | `Float` 在 MySQL 上变成 `DOUBLE(asdecimal=False)` | **不加 `asdecimal=False`，查回来是 `Decimal`，与 `float` 不相等**——"字段无损"立刻破功 |
| `:60` `rubrics` | 只增不改，带 `checksum` | 内容没变复用最新一版，变了追加一版 |
| `:98` `grading_runs` | `rubric_id` 外键指向快照 | 旧运行永远能显示当时的标准 |
| `:131` `item_results` | `kind` + `level` 可空 | 扣分项没有等级，不能硬塞进采分点的列 |

**`repository.py` 的三个设计决定**：

1. **写入是一个事务**（`:162`，整段在 `with self._engine.begin()` 里）。
   标准快照、答卷、N 次运行、逐条判定、聚合结果要么全进，要么全不进。
   半份数据比没有数据更危险——复核的人会以为那就是当时的结论。
2. **读回的对象就是领域对象**。`load_evaluation` 返回 `StoredEvaluation`，
   里面既有 `Evaluation`（samples + aggregate + route），也有运行元信息（`RunRecord`）与答卷元信息。
   不另造一套"字段名差不多"的 DTO。
3. **按运行号读回要取"包含这条运行的那次聚合"**（`:409` `_find_aggregate`），
   不能图省事取最新——重评之后，旧运行和最新结论已经不是一回事。

**两个"宁可炸也不猜"的地方**（`:112` `_level_of`、`:122` `_score_of`）：
遇到 `NULL` 直接抛，而不是补 0。把"没评成"读成"评了 0 分"是最难发现的造假。

**动手验证**（完整走一遍落库与读回）：

```bash
cd backend
rm -f /tmp/guide.db
DATABASE_URL="sqlite:////tmp/guide.db" ../.venv/bin/alembic upgrade head
../.venv/bin/python -m app.cli \
  --rubric ../data/samples/tcp-handshake/rubric.json \
  --answer ../data/samples/tcp-handshake/answer.txt \
  --response ../data/samples/tcp-handshake/response.json \
  --samples 3 --save --database-url "sqlite:////tmp/guide.db"

../.venv/bin/python -c "
from app.store import Store
s = Store.from_url('sqlite:////tmp/guide.db')
r = s.load_evaluation(answer_id=1)
print('运行次数', len(r.runs), '| 分', r.evaluation.aggregate.score, '| 路由', r.evaluation.route.route)
print('P1', r.evaluation.samples[0].items[0])
s.dispose()
"
```

（未装依赖时用 `python3` 代替 `../.venv/bin/python`，与数据库相关的部分会自动跳过。）

### ⑨ 入口：`cli.py`（约 15 分钟）

**职责**：参数解析、错误码、把结果组装成给人看的 JSON。

- `:24` `build_report`：把 `Evaluation` 摊平成展示结构。注意它挑"代表采样"的逻辑——
  证据取自"等级等于中位数**且已接地**"的那一份，避免出现"等级 1.0 但 grounded=false"的自相矛盾。
- `:101` `_save`：调 `Store`。**不建表**——建表只能走迁移。
- `:137` `main`：退出码约定。

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 评分标准不合法 |
| 3 | 模型输出不可用（`GradingError`） |
| 4 | 模型调用失败（`ProviderError`） |
| 5 | 写入数据库失败（`SaveError`） |

**要看出什么**：CLI 里没有任何业务规则，只有"读参数 → 调用 → 打印 → 定退出码"。
规则都在 `core/` 和 `pipeline.py` 里。

---

## 3. 三条主线追踪

读完之后，用这三条主线检验自己是否真的串起来了。每条都给出"该看哪几个文件"。

### 主线 A：一份答卷是怎么变成 8.0 分的

```text
rubric.json
  → core/rubric.py:57  from_dict          校验并建 Rubric
  → core/prompt.py:59  build_messages     rubric + 答案 → 消息
  → providers/deepseek.py:104  complete   调模型，拿回 JSON 字符串
  → pipeline.py:35     extract_payload    剥围栏、json.loads
  → pipeline.py:58     _parse_items       逐点校验 level 合法性
  → core/grounding.py:92 apply_grounding  证据回原文定位，定位失败判 0
  → core/aggregate.py:39 sample_score     等级 → 分值，扣分项封顶，clamp
  → 得到 SampleJudgement（一次采样）
```

### 主线 B：为什么"三次采样"能变成"一个结论"

```text
pipeline.py:137 evaluate
  → 重复 grade() n 次，每次 sample_index 递增
  → core/aggregate.py:67  aggregate
        逐点中位数 → 重算总分 → 加权离散度 → 置信度 = (1-离散度) × 接地率
  → core/routing.py:34    decide
        四项信号 → auto / human + reasons + risk
  → 得到 Evaluation（samples + aggregate + route）
```

### 主线 C：为什么改了评分标准，旧记录还是对的

```text
store/repository.py:274 _ensure_rubric
    算 schema_json 的 sha256 → 与最新版本比对
    相同 → 复用这一行；不同 → version + 1，插入新行
        ↓
grading_runs.rubric_id 外键指向那一行（永不修改）
        ↓
store/repository.py:331 load_evaluation
    从 aggregate 的 rubric_id 反查 rubrics.schema_json → from_dict → 当时那份标准
```

---

## 4. 测试地图：哪条性质被哪个文件守着

```bash
cd backend
python3 -m unittest discover -s tests -t .          # 默认命令（缺依赖时自动跳过存储用例）
```

| 测试文件 | 用例数 | 守的性质 |
| --- | --- | --- |
| `test_rubric.py` | 11 | 坏标准要报出**全部**问题；`to_dict`/`from_dict` 严格互逆 |
| `test_grounding.py` | 15 | 原文偏移准确；容错匹配不跨整卷拼凑；编造证据判 0；接地率不因"什么都没提取到"而虚高 |
| `test_pipeline.py` | 10 | 模型给的分数一律不采信；缺采分点/非法等级要显式失败 |
| `test_aggregate.py` | 10 | 偶数采样取下中位；扣分项分歧计入离散度；置信度含接地率因子 |
| `test_deepseek_provider.py` | 10 | 请求形状、状态码映射、空响应重试、配置读取（**不联网**） |
| `test_cli.py` | 5 | 报告字段齐全；错误码；`--save` 能读回 |
| `test_store.py` | 9 | 字段无损往返；标准快照不可变；重评后按旧运行号不会串到新结论 |
| `test_migrations.py` | 2 | 迁移建出的表 == `tables.py`（逐列、逐索引、逐外键） |
| `test_store_mysql.py` | 3 | 真库 `utf8mb4`；中文与 emoji 在 TEXT 与 **JSON 列**都能存活（默认跳过） |

**测试里的接缝（seam）**，读懂这三个就够了：

1. **纯函数直接单测**：`core/*` 没有依赖，想怎么测怎么测。
2. **HTTP 层注入假 transport**：`DeepSeekProvider(config, transport=...)`。
3. **Store 接真数据库**：SQLite 跑快，MySQL 验字符集；`CannedProvider` 让整条链路不联网。

---

## 5. 命令速查

```bash
# 全量测试（不需要数据库）
cd backend && python3 -m unittest discover -s tests -t .

# 只跑某一层
python3 -m unittest tests.test_aggregate -v

# 本地起库 + 建表（一条命令，可重复执行）
./scripts/dev-db.sh up
./scripts/dev-db.sh shell      # 进 MySQL 命令行

# 跑一次完整评分（离线，不花钱不联网）
cd backend
python3 -m app.cli \
  --rubric ../data/samples/tcp-handshake/rubric.json \
  --answer ../data/samples/tcp-handshake/answer.txt \
  --response ../data/samples/tcp-handshake/response.json \
  --samples 3

# 真调模型（需要 .env 里的 DEEPSEEK_API_KEY）
python3 -m app.cli --rubric ... --answer ... --live --samples 3

# 落库
python3 -m app.cli ... --save              # 用 DATABASE_URL
python3 -m app.cli ... --save --database-url "sqlite:////tmp/x.db"

# 迁移
cd backend && DATABASE_URL=... python3 -m alembic upgrade head
cd backend && DATABASE_URL=... python3 -m alembic downgrade base
cd backend && DATABASE_URL=... python3 -m alembic upgrade head --sql   # 只打印 DDL，不执行
```

---

## 6. 最容易读错的六个地方

| 读错的方式 | 实际是什么 |
| --- | --- |
| "模型打等级，服务端连分数也是模型给的吧" | 模型**只**给每个采分点的 `level` 和 `evidence`。分值、总分、满分全部服务端算。模型输出里就算有 `score` 也不会被读 |
| "接地成功 = 判定正确" | 接地**只**证明这句话真在卷子里。判定对不对要靠采分点定义得好不好 |
| "聚合就是取平均" | 逐点取中位数，再重算总分。总分绝不平均 |
| "置信度 = 1 − 离散度" | 是 `(1 − 离散度) × 接地率`。漏掉后面那项，全盘编造也会得 1.0 |
| "多次采样一定有用" | 只在模型输出真的随机时有用。思考型模型可能忽略 `temperature`，**必须实测**（`docs/experiments.md` 的 E1 就是这件事） |
| "`store` 依赖 `pipeline` 是写错了" | 故意的：`Evaluation` 是 pipeline 定义的"一次评分的产出"，store 要存它就必须认识它。依赖表已写明（架构 §3） |

---

## 7. 接口与"扩展点"

现在只有这些公开入口。要加功能，先看这里该改哪一层：

| 想做的事 | 该动哪一层 | 不该动 |
| --- | --- | --- |
| 换一家模型供应商 | 新增 `providers/<vendor>.py`，实现 `complete` | `core/`、`pipeline.py` |
| 改评分纪律/输出格式 | `core/prompt.py` 的 `SYSTEM` / `OUTPUT_SHAPE` | `providers/` |
| 调聚合或路由阈值 | `core/aggregate.py` / `core/routing.py` 的 `RoutePolicy` | `store/` |
| 加一种采分点类型 | `core/types.py` + `core/rubric.py` 的校验 + 迁移 | `cli.py` |
| 加字段落库 | `store/tables.py` + 新迁移 + `save/load` | 其它层 |
| 加 HTTP 接口 | 新建 `app/api/`（按架构 §3 应放这里） | `core/` |

`Store` 的公开成员只有五个：`from_url` / `engine` / `dispose` / `save_evaluation` / `load_evaluation`。
`create_schema` 已经被拿掉了——建表只能走迁移，`tests/support.py` 里那份是测试专用。

---

## 8. 代码库里**还没有**的东西

避免在代码里找不存在的东西（这些是后续工单）：

| 缺什么 | 属于哪张工单 | 现状 |
| --- | --- | --- |
| `app/main.py`、`app/api/` | #7 起 | 完全没有。现在唯一入口是 `cli.py` |
| 认证（口令 + cookie） | #6 | 没有 |
| 答卷批量导入 | #7 | 只能一次一份，用 `--answer` |
| 后台批量评分任务 | #8 | 没有，`evaluate` 是同步的 |
| 复核界面与人工改分 | #9 | 只有 `reviews` 表，没有读写它的代码 |
| 自然语言 → 评分标准 | #10 | `core/rubric_gen.py` **不存在**，标准得手写 JSON |
| 分数校准 | 校准层 | `core/calibrate.py` **不存在**；`calibrated_score` 恒为 `NULL` |
| 模型能力注册表 | 后续 | `providers/registry.py` **不存在** |
| 前端 | #11–#14 | `frontend/` 目录不存在 |
| token / 耗时统计 | 后续 | `grading_runs.tokens_in/latency_ms` 列存在但恒为 `NULL` |

---

## 9. 建议的阅读节奏

### 路线一：半天，只为"能跟人讲清楚这套设计"

```text
0. 本文档 §0–§1             10 分钟  建立全局图
1. core/types.py            15 分钟  认词
2. pipeline.py              20 分钟  看编排（跳过 grounding 细节）
3. core/aggregate.py        25 分钟  中位数 + 离散度 + 置信度
4. core/routing.py          10 分钟  四项信号
5. 跑一次 §5 的离线命令      10 分钟  看真实 JSON 输出
6. 本文档 §6                10 分钟  自检有没有读错
```

跳过：`store/`、`migrations/`、`providers/deepseek.py` 的实现细节。

### 路线二：两天，为了能动手改

```text
Day 1（评分内核）
  ① core/types.py            → ② core/normalize.py + grounding.py（重点，边读边跑）
  → ③ core/rubric.py          → ④ core/prompt.py
  → ⑤ providers/              → ⑥ pipeline.py
  → ⑦ core/aggregate.py + routing.py
  每节读完立刻跑该节的"动手验证"，再跑一次对应测试文件。

Day 2（系统与工程）
  ⑧ store/tables.py           → ⑧ store/repository.py（两个入口方法先读，私有方法后读）
  → migrations/versions/ 初始迁移（对照 tables.py 读）
  → ⑨ cli.py
  → tests/test_store.py + test_migrations.py（看"字段无损"和"迁移对齐"是怎么被守住的）
  → docs/ticket-notes.md（看 #4 和 #5 里那些被修掉的 bug，比读正常代码更快理解设计边界）
```

**一个提高效率的习惯**：读每一节之前，先看那一层对应的测试类名。
测试类名就是这一层的需求清单——`SaveOneEvaluation`、`RubricSnapshot`、`ConfidenceReflectsGrounding`
这些名字比注释更能说明"这段代码承诺了什么"。

---

## 10. 文档地图

| 文件 | 什么时候读 |
| --- | --- |
| `README.md` | 想快速知道"这个项目要解决什么问题" |
| `docs/architecture.md` | **设计唯一真源**。本文档所有"为什么"的最终出处 |
| `docs/codebase-guide.md` | 本文。怎么读代码 |
| `docs/roadmap.md` | 想知道"下一步做什么、为什么是这个顺序" |
| `docs/ticket-notes.md` | 想知道"哪些坑已经踩过"。**读别人修过的 bug 是理解设计边界最快的方式** |
| `docs/experiments.md` | 想知道"多采样到底有没有用"的实测数据 |
| `AGENTS.md` | 项目约定（文档语言、提交格式、每工单记录） |
