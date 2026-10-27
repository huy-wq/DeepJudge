# 代码库导读指南

> **这份文档是"怎么读"，不是"是什么"。**
> 设计真相只有一份：`docs/architecture.md`。本文件只负责把你带到那些代码面前，
> 告诉你**按什么顺序读、每一层要看出什么、哪些地方最容易读错**。
>
> 若两者冲突，以 `docs/architecture.md` 为准；发现冲突请改本文件。

**当前状态**：193 个测试通过（3 个 MySQL 真库用例默认跳过；计数来自装了依赖的 `.venv`，
裸 `python3` 会因缺少 SQLAlchemy / FastAPI 跳过存储与接口用例，数字对不上不是回归），约 9500 行（含文档与测试）。
已完成的工单：#2 单份评分闭环、#3 证据接地、#4 多采样与路由、#5 持久化、#6 认证、#7 答卷导入、#10 标准生成。
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
│   │                        rubric / json_extract / prompt / aggregate / routing）
│   ├── providers/       ← 模型适配层（唯一碰网络的地方）
│   ├── pipeline.py      ← 编排：把上面两层串成"一次评分"
│   ├── rubric_gen.py    ← 编排：把教师口述的标准变成结构化标准（三视图 + 一次重试）
│   ├── store/           ← 持久化（唯一碰 SQL 的地方）
│   ├── api/             ← HTTP 接口层：路由、认证/数据库依赖、错误映射
│   ├── importing.py      ← 答卷导入解析（纯函数，不碰库与 HTTP）
│   ├── auth.py          ← 共享口令 + 服务端会话（可撤销、可过期）
│   ├── main.py          ← FastAPI 装配（工厂函数，启动期校验口令）
│   ├── cli.py           ← 命令行入口与报告组装
│   └── config.py        ← .env 读取
├── migrations/          ← Alembic 迁移（表结构的部署真源）
├── tests/               ← 193 个用例
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
| `rubric_gen.py` | 257 | core + providers | ⑥′（与评分并列的另一条主线） |
| `core/aggregate.py` + `core/routing.py` | 194 | 仅标准库 | ⑦ |
| `store/` | 719 | SQLAlchemy | ⑧ |
| `cli.py` | 206 | 全部 | ⑨ |
| `auth.py` + `api/` + `main.py` | 约 400 | FastAPI | ⑩（另一条主线：HTTP 入口） |
| `importing.py` | 260 | 仅标准库 + core | ⑪（答卷怎么进来） |

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
| 1 | 载入并校验评分标准 | `core/rubric.py:140` `from_dict`（内部走 `:148` `parse`）→ `:224` `validate` | ✅ |
| 2 | 预处理答卷（规范化 + 偏移映射） | `core/normalize.py:12` `normalize_with_map` | ✅ |
| 3 | 组装提示词 | `core/prompt.py:59` `build_messages` | ✅ |
| 4 | **调用模型** | `providers/deepseek.py:104` `complete` | ❌ 唯一不可控的一步 |
| 5 | 解析 + 证据接地 | `core/json_extract.py:31` `extract_payload`、`pipeline.py:41` `_parse_items`、`core/grounding.py:92` `apply_grounding` | ✅ |
| 6 | 多采样聚合 | `core/aggregate.py:67` `aggregate` | ✅ |
| 7 | 服务端算分 | `core/aggregate.py:39` `sample_score`（**唯一算分入口**） | ✅ |
| 8 | 路由 + 落库 | `core/routing.py:34` `decide`、`store/repository.py:162` `save_evaluation` | ✅ |

> 八步之外还有一条更上游的线：**先有标准，才谈得上评分**。
> 教师的一段口述怎么变成这份 rubric，看 `app/rubric_gen.py`（见 §2 ⑥′）。

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

#### 中英对照：`types.py` 里的英文名词

**命名约定**：标识符一律英文，文档与注释一律中文（`AGENTS.md`）。
所以读代码时会在两种语言之间来回切——下表是切换用的字典。
**中文列是本项目文档中的固定说法**，写设计、写论文、提 issue 都用这一列，不要另造近义词。

> 这张表将来如果被前端、接口文档同时引用，应该提升成仓库根的 `GLOSSARY.md`
> （`AGENTS.md` 规定的领域文档布局）。现在只有读代码的人需要它，先留在这里，
> 避免同一份词表两处维护、迟早对不上。

**类（一行一个概念）**

| 代码 | 中文 | 一句话 | 易错点 |
| --- | --- | --- | --- |
| `Level` | 等级 / 档次 | 某个采分点的一档：等级标识 + 该档分值 + 判分标准 | 不是"分数"，它装的是**一档** |
| `RubricItem` | **采分点** | 一个可独立给分的观察点 | 全项目最重要的词。`item` 在别处是"项目"，这里**只**指采分点 |
| `Deduction` | 扣分项 | 命中就要扣分的负面观察点 | `penalty` 与 `cap` **都是负数** |
| `Rubric` | 评分标准 | 一道题的一整份标准（题干 + 采分点 + 扣分项 + 规则） | 不是"评分结果" |
| `ItemJudgement` | 采分点判定 | 模型对**某一个**采分点的一次判定 | 与 `RubricItem` 配对：一个是标准，一个是判定 |
| `DeductionJudgement` | 扣分项判定 | 模型对某一个扣分项的一次判定 | 没有 `level`，因为扣分项不分档 |
| `SampleJudgement` | 单次采样判定 | 一次模型调用产出的完整结果 | 不是"最终结论"；最终结论是聚合出来的 `Aggregate`（见 `aggregate.py`） |
| `EPS` | 浮点容差 | 比较浮点是否相等时用的阈值（`1e-9`） | `0.1 + 0.2 != 0.3`，所以不能直接 `==` |
| `UNGROUNDED_NOTE` | 未接地提示 | 证据定位失败时拼进 `reason` 的固定文案 | 它是**给人看的说明**，不参与判断 |

**`Level` 的字段**

| 代码 | 中文 | 说明 |
| --- | --- | --- |
| `level` | 等级标识 | 序数，如 `1.0` / `0.5` / `0.0`。**只能取该采分点声明过的值** |
| `score` | 该档分值 | 线性量，如权重 3 的点：1.0→3.0、0.5→1.5 |
| `criterion` | 判分标准 | 这一档的判定依据，会被写进提示词 |

> 一个字段叫 `level`、另一个叫 `score`，是刻意的：**聚合在等级上做中位数，算分在分值上做加权**，
> 混成一个字段就两头都做不好（平均等级可能算出非法档位，平均分值对不上任何一次真实判定）。

**`RubricItem`（采分点）的字段**

| 代码 | 中文 | 说明 |
| --- | --- | --- |
| `id` | 采分点编号 | 如 `P1`。**全局唯一**，判定结果靠它对上 |
| `desc` | 采分点描述 | 这个点要求学生答出什么 |
| `weight` | 权重 | 该点的满分。所有采分点权重之和必须等于 `Rubric.total_score` |
| `levels` | 等级表 | 该点允许的全部档次 |
| `must_hit` | 必答点 | 标记"核心采分点" |
| `acceptable` | 可接受表述 | 同义说法白名单（如"SYN 报文 / 同步标志位"） |
| `allowed_levels` | 允许的等级取值（属性） | 从 `levels` 去重排序得来，给校验和提示词用 |
| `score_for()` | 按等级取分值（方法） | **等级 → 分值的唯一换算入口** |

> ⚠️ `must_hit` 与 `acceptable` **目前只是提示，不是服务端规则**：它们只被 `core/prompt.py:46-49`
> 拼进提示词告诉模型，代码里没有任何地方因为 `must_hit=True` 而强制判 0。
> 读到这里不要以为"未命中必答点会被自动判 0"——那件事还没实现。

**`Deduction`（扣分项）的字段**

| 代码 | 中文 | 说明 |
| --- | --- | --- |
| `penalty` | 扣分幅度 | **负数**，如 `-2` |
| `cap` | 封顶 | **负数**；校验规则是 `penalty ≥ cap`（架构 §4.1）。⚠️ 见下方说明 |
| `effective()` | 实际生效的扣分（方法） | 返回 `max(penalty, cap)`；在上述校验规则下**恒等于 `penalty`** |

> ⚠️ **`cap` 目前不会改变任何结果**，这是读代码时要意识到的：校验强制 `penalty ≥ cap`，
> 而两个负数取 `max` 又必然取到 `penalty`——也就是说被拒绝的那种输入（`penalty < cap`）
> 恰恰是唯一能让 `cap` 生效的情况。文档（架构 §4.1）与代码在这一点上是一致的，
> 所以这不是实现 bug，而是"`cap` 为将来的**多次累计**预留、当前单次二值命中用不到"。
> 要不要保留这个字段、或者改成 `min` 让它真的封顶，是一个待定的设计问题（见 §11）。

**`Rubric`（评分标准）的字段**

| 代码 | 中文 | 说明 |
| --- | --- | --- |
| `question_id` | 题目编号 | 如 `Q-01` |
| `stem` | 题干 | 题目原文 |
| `reference_answer` | 参考答案 | 会写进提示词（`core/prompt.py:65`），不进最终报告 |
| `total_score` | 满分 | 必须等于权重之和（容差 `1e-9`） |
| `items` | 采分点列表 | `list[RubricItem]` |
| `deductions` | 扣分项列表 | 可以为空 |
| `rules` | 评分规则开关 | `allow_inference`、`synonym_ok`、`ignore_length` 三个开关。见下方说明 |
| `item()` | 按编号取采分点（方法） | 找不到抛 `KeyError` |

> ⚠️ `rules` 目前是**死开关**：`from_dict` 会解析它、`to_dict` 会存快照，
> 但没有任何代码读它——`core/prompt.py` 的六条纪律是硬编码在 `SYSTEM` 里的，
> 并没有按 `rules` 的值变化。也就是说改了 `rubric.rules` 不会改变模型行为。
> 这是一处"看起来能配、实际不生效"的地方，改评分纪律目前只能改 `SYSTEM` 正文。

**判定对象的字段（`ItemJudgement` / `DeductionJudgement`）**

| 代码 | 中文 | 说明 |
| --- | --- | --- |
| `item_id` / `id` | 对应编号 | 采分点用 `item_id`，扣分项用 `id`——命名不一致是历史遗留，读时注意 |
| `level` | 判定等级 | 仅采分点有 |
| `hit` | 声称命中 | **模型说"这份答卷提到了"**，不代表得分，也不代表证据是真的 |
| `evidence` | 证据 | 模型引用的原文片段 |
| `span` | 原文区间 | `(start, end)`，**原文**的字符下标，不是规范化后的 |
| `grounded` | 已接地 | 证据真的在答卷里定位到了 |
| `reason` | 判定理由 | 给复核者看的解释 |
| `mark_grounded()` | 标记已接地（方法） | 同时写入 `span` |
| `mark_ungrounded()` | 标记未接地（方法） | **采分点**：`level` 置 0；**扣分项**：不生效。两者的 `hit` 都保留 |

> `hit` 与 `grounded` 是两个独立事实，这是全项目最容易混的一对：
> `hit=True, grounded=False` = 模型声称命中，但证据是编的（或改写得太离谱）。
> 这种情况**保留 `hit` 供审计**，但**按 0 分计**。

**`SampleJudgement` 的字段与方法**

| 代码 | 中文 | 说明 |
| --- | --- | --- |
| `items` | 逐点判定 | `list[ItemJudgement]`，顺序与 rubric 一致 |
| `deductions` | 扣分判定 | `list[DeductionJudgement]` |
| `comment` | 评语 | 面向学生，不许泄露参考答案 |
| `score` | 本次采样得分 | 服务端算出来的，**不是**模型给的 |
| `max_score` | 满分 | 由 rubric 带下来，方便单独展示一次采样 |
| `levels()` | 各点等级映射（方法） | `{item_id: level}`，聚合要用 |
| `grounding_rate()` | 接地率（方法） | 分母**只算声称命中的项**；全都没命中时返回 `1.0` |
| `ungrounded_ids()` | 未接地的编号（方法） | 只收 `hit=True 且 grounded=False` 的 |

**一个值得注意的写法：只有 `Level` 是 `frozen=True`**

```python
@dataclass(frozen=True)
class Level:        # 只读：等级表是标准的组成部分，建好就不该改
```

其余类都是可变的，因为接地校验是**就地修改**判定对象的
（`grounding.apply_grounding` 直接改 `hit`/`span`/`level`/`reason`）。
如果哪天要把判定也改成不可变，`apply_grounding` 就得改成返回新对象——
这是一个真实的权衡，不是随手写的。

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
| `:59` `RubricError` | 带 `issues`（结构化，含 `blocking`）；`problems` 是派生出的句子列表 |
| `:148` `parse` | **收集式**解析：缺字段、类型错、语义错一起攒，返回 `ParsedRubric`，不抛 |
| `:140` `from_dict` | `parse` 的包装：有阻断项才抛 |
| `:158` `required` | 局部闭包，省掉每行 `try/except` |
| `:218-220` | **不再**过滤"权重合计"：差额是最该看到的数字，结构错乱时更需要它 |
| `:224` `validate` | 语义规则：权重和、id 唯一、等级单调、分值域、扣分封顶；每条带 `code` 与 `blocking` |
| `:344` `to_dict` | **与 `from_dict` 严格互逆**，供入库快照与前端展示 |

**这是本项目"报错也要设计"的样板**：用户填错标准时，最烦的是一改一报错。
所以解析器绝不早退，而是把能查的全查完。

**`to_dict` 为什么必须严格互逆**："标准快照"这四个字的全部意义在于
存进去的和读出来的是同一份标准。`tests/test_rubric.py` 里 `SerializeRubric` 专门守这条。

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

读的顺序：`core/json_extract.py:31` `extract_payload` → `_parse_items`（`:41`）→ `grade`（`:88`）→ `evaluate`（`:124`）。

| 位置 | 在干什么 | 读的时候注意 |
| --- | --- | --- |
| `core/json_extract.py:31` `extract_payload` | 剥掉 ```` ```json ```` 代码围栏并解析 | 模型经常不听话地包一层 |
| `:20` `GradingError` | 带错误码：`INVALID_JSON` / `NOT_OBJECT` / `MISSING_ITEM` / `INVALID_LEVEL` | 这四种都是"输出不可用"，不是"得了 0 分" |
| `:41` `_parse_items` | 逐个采分点找模型给的条目 | 漏了任何一个就抛 `MISSING_ITEM`，不静默补 0 |
| `:88` `grade` | 调 provider → 解析 → 接地 → **用 `sample_score` 算分** | 注意它完全不读模型给的分数 |
| `:118` `Evaluation` | `samples + aggregate + route` 三件套 | 后面 store 的返回类型就是它 |

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

### ⑥′ 另一条主线：`app/rubric_gen.py`（约 25 分钟）

**职责**：教师给"题干 + 满分 + 一段口述的标准"，这里调一次模型，产出**可评分的结构化标准**。

它不在评分的八步里，是比评分更上游的一条线：标准先有，才谈得上评分。

| 位置 | 在干什么 | 读的时候注意 |
| --- | --- | --- |
| `:29` `MAX_ATTEMPTS` | 重试上限 2（原调用 + 1 次） | 与"自动重试一次"的验收一致，不要调大 |
| `:31` `GEN_SYSTEM` | 六条纪律 | 第 1 条是核心：**不得新增教师未提及的采分点** |
| `:61` `GeneratedRubric`（`views()` 在 `:74`） | 成功结果：标准 + 摘要 + 提示项 + 尝试次数 + 原始输出 | `views()` 一次给全三视图；`issues` 里只剩仅提示项（阻断项会让它抛错） |
| `:92` `RubricGenerationError` | 重试后仍失败 | 带 `issues`（给教师看）与 `responses`（给排查用） |
| `:118` `build_generation_messages` | 组装提示词 | 教师原话放最后、用 `<standard>` 包住 |
| `:142` `preview_rows` | 预览表 | 纯数据整形，给前端渲染表格 |
| `:163` `generate_rubric` | 主循环：调模型 → 校验 → 不合法就带上错误重试 | 见下面第 3 条 |
| `:229` `_with_server_owned_fields` | **服务端改写题目信息** | 模型给的 `total_score`/`stem`/`question_id` 一律丢弃 |

**三处必须看出来的设计**：

1. **三视图各由谁产出，是不对称的。**
   - 摘要：模型写（`_summary_of`），缺失时服务端用采分点拼一句话兜底（`_compose_summary`）。
   - 预览表：服务端从结构化结果生成（`preview_rows`），模型碰不到。
   - 校验清单：服务端算（`core/rubric.validate`）。

   只有"对不对"需要自然语言；"全不全"和"合不合法"必须是确定性的。

2. **校验清单必须能分类，不能只给中文句子。**
   `core/rubric.py:25` 的 `RubricIssue(code, message, blocking)`：`code` 给程序分支，
   `message` 给教师看，`blocking` 决定拦不拦保存。`RubricError.problems` 保留为派生属性，
   所以旧调用方（`cli.py`）一行都不用改。

3. **重试不是"再来一次"，是"带着错误再来一次"。**
   `generate_rubric` 把上一轮模型原话作为 `assistant` 消息、把问题清单作为新的 `user` 消息追加上去，
   并在里面**明确禁止补点**。没有这一步，模型会原地重复同一个错误。

**动手验证**（离线，不花钱）：

```bash
cd backend
python3 scripts/gen_rubric.py \
  --stem "简述 TCP 三次握手的过程，并说明其目的。" --total 10 \
  --standard "三个要点：客户端发 SYN 给 3 分；服务器回 SYN+ACK 给 4 分；客户端回 ACK 给 3 分。" \
  --response ../data/samples/tcp-handshake/generation.json
```

三视图会依次打印出来。再试一次"模型少给一个采分点"——它应当重试一次，然后报
`还有 3 分未分配`，且**不会**替你补出第三个点（把 `generation.json` 里的 P3 删掉再跑即可）。

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

### ⑩ HTTP 入口与认证：`app/main.py` + `app/auth.py` + `app/api/`（约 20 分钟）

**职责**：让管理端"只有拿着口令的人能用"。这是唯一一条**不服务于评分**的线，
但它是所有后续接口的地基。

| 位置 | 在干什么 | 读的时候注意 |
| --- | --- | --- |
| `app/auth.py:43` `AdminAuth` | 口令校验 + 会话登记表 | 不读环境变量、不碰 HTTP，所以过期能用"拨表"测 |
| `:72` `login` | `compare_digest` 比对，通过则生成随机 sid 并登记 | 用 `==` 会按首字节提前返回，泄漏"前几位对了" |
| `:87` `verify` | 缺失 / 伪造 / 过期 / 已退出，一律 `AuthError` | 四种失败对外是同一句话，避免变成探测手段 |
| `:104` `logout` | 从登记表里删掉 | **这就是"退出后原凭据立即不可用"的全部实现** |
| `app/api/deps.py:22` `require_admin` | 受保护路由的依赖 | 读 cookie → `verify` → 失败 401。路由里不写 `if not logged_in` |
| `app/api/routes.py:32` `register` | 登录 / 退出 / 会话 / 健康检查 | 只读写 cookie，没有判断逻辑 |
| `app/main.py:44` `create_app` | 装配 + 启动期校验口令 | 用工厂函数：模块级 `app` 会让"没配口令"在 import 期就炸 |

**这一层最该看出的一个取舍**：架构最初写的是"签名 cookie"（无状态），
但验收要求"退出后原凭据立即不可用"——签名能证明"是我发的"，不能证明"没被撤销"。
所以改成了**服务端登记表**。`tests/test_api_auth.py` 里那条"退出后重放旧 cookie 必须 401"
就是钉这个差别的；把它删掉，你就退回到"客户端删了就算退出"。

**动手验证**（真起服务，不依赖数据库）：

```bash
cd backend
ADMIN_PASSWORD=devpass python3 -m uvicorn app.main:create_app --factory --port 8000 &
# 无凭据 → 401
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/auth/session
# 错口令 → 401，且只说"口令错误"
curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'content-type: application/json' -d '{"password":"nope"}'
# 正确口令 → 204 并下发 cookie
curl -s -c /tmp/c.txt -o /dev/null -w '%{http_code}\n' \
  -X POST http://127.0.0.1:8000/api/auth/login -H 'content-type: application/json' -d '{"password":"devpass"}'
# 带 cookie → 200
curl -s -b /tmp/c.txt http://127.0.0.1:8000/api/auth/session
```

**读完要能回答**：为什么 `GET /api/health` 不需要登录？
（因为"受保护"指的是管理接口；健康检查是给部署探针用的，登录反而没法探。）

### ⑪ 答卷怎么进来：`app/importing.py` + `app/api/answers.py`（约 20 分钟）

**职责**：把教师手里的 CSV 或一段粘贴文本，变成"可入库的行 + 坏行清单"。

这一层只有一个真问题：**别让学号漏出去**。所有设计都围着它转。

| 位置 | 在干什么 | 读的时候注意 |
| --- | --- | --- |
| `app/importing.py:99` `parse_answers` | 唯一入口，永远返回 `ParsedImport`，不抛 | 坏行进 `problems`，不中断整份文件 |
| `:61` `ParsedRow` | 一行好数据：行号 / 题号 / 答案 | 学号列**不在这里**，读到就丢了 |
| `:68` `ProblemRow` | 一行坏数据：**只有**行号与原因 | 刻意没有原文：坏行里哪列是学号无从判断 |
| `:80` `ParsedImport` | 解析结果 | `ok=False` 时 `error` 是一句人话，`rows` 为空 |
| `app/core/normalize.py:40` `content_hash` | 查重指纹 | 规范化后取 sha256：全角/半角、多空格都算同一份 |
| `app/api/answers.py:20` `preview` | 只解析 + 查重，**不写库** | 取消导入就是只调它 |
| `:92` `import` | 拿同一段原文**重新解析**后单事务入库 | 不接收客户端回传的行 |
| `app/api/deps.py:40` `require_store` | 没配数据库就 503 | 让认证与前端能在没 MySQL 的机器上跑 |

**三处必须看出的设计**：

1. **预览与入库不共享"解析结果"，只共享"解析器"。**
   `import` 会拿同一份 `text` 再解析一遍。若改为接收前端回传的行，预览说 45 行、入库 44 行这种
   偏差就没人能发现。

2. **坏行只报行号。**
   这是隐私取舍，不是偷懒：坏行的列角色未定，回显原文等于把学号送进响应体与日志。
   测试 `test_broken_rows_never_echo_the_original_line` 钉住它。

3. **脱敏编号与学号无关。**
   `S-0001` 是按题号顺序生成的序号（`store/repository.py:384` `import_answers`），
   与真实的 2024001 没有任何对应关系——否则"脱敏"只是换了个字段名。

**动手验证**（真库 + 真 HTTP，SQLite 即可）：

```bash
cd backend
rm -f /tmp/imp.db
DATABASE_URL="sqlite:////tmp/imp.db" python3 -m alembic upgrade head
DATABASE_URL="sqlite:////tmp/imp.db" ADMIN_PASSWORD=devpass \
  python3 -m uvicorn app.main:create_app --factory --port 8000 &
# 先建题目（保存一份标准）
python3 -m app.cli --rubric ../data/samples/tcp-handshake/rubric.json \
  --answer ../data/samples/tcp-handshake/answer.txt \
  --response ../data/samples/tcp-handshake/response.json \
  --save --database-url "sqlite:////tmp/imp.db"
# 登录后预览（把 CSV 塞进 JSON 用 python3 拼，别用 sed 手拼转义）
curl -s -c /tmp/c.txt -X POST localhost:8000/api/auth/login \
  -H 'content-type: application/json' -d '{"password":"devpass"}'
python3 -c 'import json;print(json.dumps({"source":"csv","text":open("../data/samples/tcp-handshake/answers.csv",encoding="utf-8").read()}))' > /tmp/body.json
curl -s -b /tmp/c.txt -X POST localhost:8000/api/answers/preview \
  -H 'content-type: application/json' --data-binary @/tmp/body.json
```

**读完要能回答**：为什么"取消导入不产生任何数据"这条验收几乎没有实现成本？
（因为预览和入库是两条独立的路由，前端不点确认就不会调 import。）

---

## 3. 三条主线追踪

读完之后，用这三条主线检验自己是否真的串起来了。每条都给出"该看哪几个文件"。

### 主线 A：一份答卷是怎么变成 8.0 分的

```text
rubric.json
  → core/rubric.py:140 from_dict          校验并建 Rubric（内部 parse）
  → core/prompt.py:59  build_messages     rubric + 答案 → 消息
  → providers/deepseek.py:104  complete   调模型，拿回 JSON 字符串
  → core/json_extract.py:31 extract_payload  剥围栏、json.loads
  → pipeline.py:41     _parse_items       逐点校验 level 合法性
  → core/grounding.py:92 apply_grounding  证据回原文定位，定位失败判 0
  → core/aggregate.py:39 sample_score     等级 → 分值，扣分项封顶，clamp
  → 得到 SampleJudgement（一次采样）
```

### 主线 B：为什么"三次采样"能变成"一个结论"

```text
pipeline.py:124 evaluate
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
| `test_rubric.py` | 22 | 坏标准要报出**全部**问题；阻断项与仅提示项分类正确；结构错误时仍报差额；`to_dict`/`from_dict` 严格互逆 |
| `test_grounding.py` | 15 | 原文偏移准确；容错匹配不跨整卷拼凑；编造证据判 0；接地率不因"什么都没提取到"而虚高 |
| `test_pipeline.py` | 10 | 模型给的分数一律不采信；缺采分点/非法等级要显式失败 |
| `test_aggregate.py` | 10 | 偶数采样取下中位；扣分项分歧计入离散度；置信度含接地率因子 |
| `test_deepseek_provider.py` | 12 | 请求形状、状态码映射、空响应重试、配置读取（含生成/评分模型分开配）（**不联网**） |
| `test_rubric_gen.py` | 20 | 生成提示词含纪律；重试带上错误且禁止补点；模型改不了 `rules`/`must_hit`；三视图各就各位 |
| `test_gen_rubric_script.py` | 6 | 生成脚本的退出码（含模型调用失败）与三视图输出；保存的标准能直接喂给评分 |
| `test_cli.py` | 5 | 报告字段齐全；错误码；`--save` 能读回 |
| `test_store.py` | 17 | 字段无损往返；标准快照不可变；重评后按旧运行号不会串到新结论；导入与查重 |
| `test_migrations.py` | 2 | 迁移建出的表 == `tables.py`（逐列、逐索引、逐外键） |
| `test_store_mysql.py` | 3 | 真库 `utf8mb4`；中文与 emoji 在 TEXT 与 **JSON 列**都能存活（默认跳过） |
| `test_importing.py` | 25 | 两种来源、表头（含空列不错位）、列数、坏行、BOM/CRLF、引号内换行、**坏行不回显学号** |
| `test_api_answers.py` | 19 | 预览不写库、按题号查重（跨题不误判、批内标重复）、未登录 401、无库 503、422 不回显内容 |
| `test_auth.py` | 13 | 口令校验、会话过期（拨表不 sleep）、退出即撤销、口令为空拒绝启动 |
| `test_api_auth.py` | 14 | 401 的三种来源、登录下发 cookie、错口令不透露原因、退出后重放旧 cookie 必须 401 |

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

# 起 HTTP 服务并带一个 SQLite 库（业务接口需要 DATABASE_URL，否则 503）
cd backend && rm -f /tmp/dj.db
DATABASE_URL="sqlite:////tmp/dj.db" python3 -m alembic upgrade head
DATABASE_URL="sqlite:////tmp/dj.db" ADMIN_PASSWORD=devpass \
  python3 -m uvicorn app.main:create_app --factory --port 8000

# 起 HTTP 服务（不需要数据库；没有 ADMIN_PASSWORD 会直接失败）
cd backend && ADMIN_PASSWORD=devpass python3 -m uvicorn app.main:create_app --factory --port 8000

# 从教师原话生成结构化标准（离线；加 --live 则真调模型）
cd backend
python3 scripts/gen_rubric.py --stem "题干" --total 10 --standard "教师原话" \
  --response ../data/samples/tcp-handshake/generation.json --save-rubric /tmp/rubric.json

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
| 加 HTTP 接口 | `app/api/*.py` 里加 `register(app)`，受保护接口挂 `Depends(require_admin)`，要库的挂 `Depends(require_store)` | `core/`、`auth.py` |
| 改导入格式（如 Excel） | `app/importing.py` 加一个 `source` 分支 | `store/`、`api/` |
| 换会话存储（Redis / 数据库） | 只换 `AdminAuth` 的登记表实现 | `api/`、`main.py` |

`Store` 的公开成员：`from_url` / `engine` / `dispose` / `save_evaluation` / `load_evaluation` /
`get_question` / `find_duplicates` / `import_answers` / `list_answers` / `count_answers`。
`create_schema` 已经被拿掉了——建表只能走迁移，`tests/support.py` 里那份是测试专用。

---

## 8. 代码库里**还没有**的东西

避免在代码里找不存在的东西（这些是后续工单）：

| 缺什么 | 属于哪张工单 | 现状 |
| --- | --- | --- |
| 评分/复核接口 | #8、#9 | `app/api/` 目前只有认证、健康检查、答卷导入与答卷列表 |
| 导入批次撤销 | 未定 | 只能按 answer_id 删；验收只要求"取消导入不产生数据"，已满足 |
| 后台批量评分任务 | #8 | 没有，`evaluate` 是同步的 |
| 复核界面与人工改分 | #9 | 只有 `reviews` 表，没有读写它的代码 |
| 自然语言 → 标准（HTTP 接口） | #13 | 生成逻辑已在 `app/rubric_gen.py`，但只有脚本入口，没有 REST |
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
  → ⑥′ app/rubric_gen.py（另一条主线，可与 ⑥ 并行读）
  → ⑦ core/aggregate.py + routing.py
  每节读完立刻跑该节的"动手验证"，再跑一次对应测试文件。

Day 2（系统与工程）
  ⑧ store/tables.py           → ⑧ store/repository.py（两个入口方法先读，私有方法后读）
  → migrations/versions/ 初始迁移（对照 tables.py 读）
  → ⑨ cli.py
  → tests/test_store.py + test_migrations.py（看"字段无损"和"迁移对齐"是怎么被守住的）
  → ⑩ app/main.py → app/auth.py → app/api/deps.py → app/api/routes.py
  → docs/ticket-notes.md（看 #4 / #5 / #6 里那些被修掉的 bug，比读正常代码更快理解设计边界）
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

---

## 11. 读代码时容易撞上的六处"名不副实"

这三处不是 bug，但**只看代码或只看文档都会误解**。写论文、答辩、改代码之前先读一遍。

### 11.1 `Deduction.cap`（封顶）当前不生效

- **现象**：校验强制 `penalty ≥ cap`，而 `effective() = max(penalty, cap)`，两个负数取 `max` 必然取到
  `penalty`。被校验拒绝的那种输入（`penalty < cap`）恰恰是唯一能让 `cap` 起作用的情况。
- **影响**：单次二值命中下 `cap` 不改变任何分数。
- **状态**：文档（架构 §4.1）与代码一致，属"为多次累计预留"。
  要不要保留这个字段、或把 `max` 改成 `min` 让它真的封顶，**尚未拍板**。

### 11.2 `must_hit`（必答点）只是提示，不是规则

- **现象**：`must_hit` 与 `acceptable` 只被 `core/prompt.py:46-49` 拼进提示词告诉模型，
  服务端没有任何地方因 `must_hit=True` 而强制判 0。
- **影响**：模型若无视这条提示，必答点未命中也不会被系统拦住。
- **状态**：有意的——"判 0"应该是评分标准里最低档的定义，不该变成代码里的特殊分支。
  但读代码时别以为系统会替你把关。

### 11.3 同一个"编号"有两个字段名

- **现象**：`ItemJudgement` 用 `item_id`，`DeductionJudgement` 用 `id`；`RubricItem` 用 `id`。
- **影响**：写序列化或适配代码时容易拿错属性名。
- **状态**：历史遗留。改名会牵动 `store/` 的读写与既有数据，暂不动；
  统一命名是一次值得做的小重构，记在这里提醒后来者。

### 11.4 最高档分值可以不等于权重

- **现象**：校验只要求 `score ≤ weight`。若权重 3 的采分点最高档写 `2.5`，该点满分不可达。
- **影响**：该采分点永远拿不满，"按权重算分"名义上成立、实际上白丢 0.5 分。
- **状态**：已在 `docs/ticket-notes.md` 记为待拍板。加硬校验让规则名副其实，
  但会约束评分标准的写法。

### 11.5 `from_dict` 只因为"阻断项"报错，要全部问题得用 `parse`

- **现象**：`core/rubric.py` 里有两个入口——`parse()` 返回 `ParsedRubric(rubric, issues)`（不抛）、
  `from_dict()` 是它的一层包装（有阻断项才抛 `RubricError`）。
  仅提示项（满分不可达、判定依据为空或含糊、描述重复）不拦保存，因此 `from_dict` 不会提它们。
- **影响**：一份"能算分但不可靠"的标准可以正常载入。这不是校验变松了，而是把
  "能不能存"和"值不值得存"分开了（架构 §5.3）。
- **怎么看到提示**：走 `parse()` 拿 `issues`；`app.cli` 会把仅提示项打到 stderr；
  生成流程用 `GeneratedRubric.views()["checklist"]` 拿到完整清单。

### 11.6 `rubric.rules`（规则开关）是死开关

- **现象**：`allow_inference` / `synonym_ok` / `ignore_length` 三个开关会被解析、
  会存进标准快照，但**没有任何代码读它们**。`core/prompt.py` 的六条纪律硬编码在 `SYSTEM` 字符串里。
- **影响**：改了 `rubric.rules` 不会改变模型行为，也不会改变算分。看起来可配，实际不生效。
- **状态**：占位字段。要让它生效，得把 `SYSTEM` 拆成按开关拼装——这是一个明确的小工单，
  但改提示词会牵动已有的方差实验结论（E1），所以没在这一票里顺手做。
