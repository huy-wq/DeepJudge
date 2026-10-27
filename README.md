# DeepJudge · 基于 DeepSeek 的主观试题评判研究与设计

> 设计并实现一个**可解释、可复现、可追责**的主观题自动评判系统。

**课题定位：工程型（系统设计与实现）。** 不追求算法创新，重点是把"大模型评分"做成可运行、可复核的系统，
并对关键设计取舍给出论证与验证。

## 一、要解决的问题

主观题自动评分的难点不在"让模型打个分"——那只要一段提示词。难点是这三件事：

| 问题 | 表现 | 应对 |
| --- | --- | --- |
| 一致性 | 同一份答卷重复评分波动 1~3 分 | 采分点级判定 + 多次采样聚合 + 分数校准 |
| 可解释 | 只给总分，教师不认可、学生不服 | 逐点判定 + 证据原文定位 |
| 可追责 | 模型编造理由，出错无法定位 | 证据接地校验 + 版本化 + 审计 |

## 二、一句话思路

> 把"让模型给总分"改造成"让模型逐条判定采分点、并给出原文证据"，分数由服务端按权重计算；
> 再用多次采样的一致性作为置信度，低置信转人工，最后用人工样本校准刻度。

完整设计（数据流八步、模块划分、数据结构、决策与边界、验证方式）见 **[docs/architecture.md](docs/architecture.md)**。
这份文档是唯一的设计真源，不再拆分成多份。

## 三、仓库结构

```text
DeepJudge/
├── README.md            # 课题定位与导航
├── AGENTS.md            # 项目约定（供 agent 读取）
├── docs/
│   ├── architecture.md  # 唯一设计真源
│   ├── codebase-guide.md # 代码库导读（按什么顺序读、每层看什么）
│   ├── roadmap.md       # 功能实现清单（顺序与依赖）
│   ├── demo.md          # 10 分钟演示脚本与降级路径（答辩用）
│   ├── ticket-notes.md  # 工单问题记录（遇到的坑与未决问题）
│   ├── experiments.md   # 实验记录（真实调用的设置与结果）
│   ├── agents/          # issue tracker / 标签 / 领域文档约定
│   └── tooling.md       # 本地工具配置
├── backend/             # Python + FastAPI 评分引擎（结构见架构文档第 3 节）
│   ├── migrations/      # Alembic 迁移（表结构的部署真源）
│   └── tests/           # 单测与真库验证
├── scripts/dev-db.sh    # 本地数据库一键起库 + 升级表结构
├── scripts/demo.sh      # 演示数据准备（seed / reset）
├── docker-compose.yml   # 本地 MySQL 8（utf8mb4）
├── frontend/            # 管理端（Vue 3 + Vite + TypeScript + Element Plus）
└── data/                # 本地答卷与标注集，默认不入库
```

## 四、技术栈

| 层 | 选型 | 状态 |
| --- | --- | --- |
| 语言 / 运行时 | Python 3.10+ | **已定** |
| Web 框架 | FastAPI | **已定** |
| 服务形态 | 单体后端 | **已定** |
| 存储 | MySQL 8（`utf8mb4`）+ Alembic 迁移 | **已实现** |
| 认证 | 共享口令 + 服务端会话 cookie | **已实现** |
| 任务执行 | 进程内线程池（可重试、可中断恢复） | **已实现** |
| 前端 | Vue 3 + Vite + TypeScript + Element Plus | **已实现**（登录 / 标准 / 导入与评分 / 复核） |
| 前端测试 | Vitest + @vue/test-utils（jsdom） | **已接入** |
| LLM 供应商与参数 | DeepSeek（`providers/deepseek.py`，标准库实现） | **已接入** |
| 部署 | 本地优先 | 本地演示路径见 `docs/demo.md`；公网部署未做 |

评分引擎只依赖 `GradingProvider` 一个接口（见架构文档第 2、3 节），换模型不影响流水线与测试。

## 五、LLM API 参考（待补充）

以下是 2026-10 核实的 DeepSeek API 现状，**仅作参考**，以接入时官方文档为准：

| 项 | 值 |
| --- | --- |
| 模型 | `deepseek-flash`、`deepseek-v4-pro` |
| 上下文 / 输出 | 1M / 最大 384K |
| 结构化输出 | `response_format={"type": "json_object"}`，偶发空响应需重试 |
| 采样确定性 | 部分思考型模型忽略 `temperature` → 稳定性只能靠多采样 |
| 价格（flash） | 输入 1~2 元/M（缓存命中更低），输出 4~8 元/M；空闲时段半价 |

## 六、快速开始

```bash
git clone git@github.com:huy-wq/DeepJudge.git && cd DeepJudge
python3 -m venv .venv && source .venv/bin/activate
pip install -e backend        # 引擎 + 存储依赖（sqlalchemy / alembic / pymysql）
cp .env.example .env          # 填 DEEPSEEK_API_KEY；DATABASE_URL 指向本地库
```

**起库并建表（一条命令，可重复执行）**

```bash
./scripts/dev-db.sh up
```

它做三件事：用 `docker-compose.yml` 起 MySQL 8（端口 3307，避免和本机 3306 撞）、等健康检查通过、
在 `backend/` 下跑 `alembic upgrade head`。要连数据一起重来用 `./scripts/dev-db.sh reset`。

拉不动官方镜像时换镜像站：`MYSQL_IMAGE=docker.1ms.run/library/mysql:8 ./scripts/dev-db.sh up`

**起 HTTP 服务**

```bash
cd backend
ADMIN_PASSWORD=换个只有你知道的口令 python3 -m uvicorn app.main:create_app --factory --port 8000
```

没有 `ADMIN_PASSWORD` 会直接启动失败——不会静默跑起来一个谁都能进的管理端。
所有管理接口都在口令 + 会话之后；先换一个 cookie：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/auth/session        # 401
curl -s -c /tmp/c.txt -X POST http://127.0.0.1:8000/api/auth/login \
     -H 'content-type: application/json' -d '{"password":"换个只有你知道的口令"}'        # 204
curl -s -b /tmp/c.txt http://127.0.0.1:8000/api/auth/session                            # 200
```

**起前端（管理端骨架）**

```bash
cd frontend
npm install            # 首次
npm run dev            # http://127.0.0.1:5173
```

开发服务器把 `/api` 代理到 `http://127.0.0.1:8000`，所以前后端同源、cookie 能正常带上。
先按上一节把后端跑起来（`ADMIN_PASSWORD=...`），打开 5173 会看到登录页：

- 未登录访问任意管理路径 → 跳转登录页，并记住原地址；
- 口令正确 → 进入首页；刷新页面会重新确认服务端 cookie，登录态保持；
- 退出 → 回到登录页，服务端会话同时撤销；
- 接口失败/后端没起 → 弹出可读提示，页面不会白屏（渲染异常也由 `ErrorBoundary` 兜住）。

登录后进入 **评分标准**（`/rubrics`）就是一个完整的教师闭环：

1. 新建标准：左侧填题干、满分、评分标准原文 → **生成草稿**；
2. 右侧同屏看摘要、预览表、校验清单；直接改某行的描述/权重/等级分值，清单实时更新；
3. 结构错误红色、保存按钮禁用；语义提示黄色，勾选"已知悉"后才能保存；
4. 保存成功会跳到 `/rubrics/{id}`，之后可在列表里重新打开；
5. 已保存的版本可以写一条意见**重新生成**，响应里的变更清单会列出模型改了哪些点。

**导入与评分**（`/grading`）把剩下三步串成一页：

1. 选一个 UTF-8 的 CSV（题号列不能省；Excel 先另存为 CSV）或粘贴单份答案；
   预览会列出重复项与坏行（只报行号，不回显学号），确认后才入库；
2. 选题号、勾选答卷、设置每份采样次数 → **开始评分**；
3. 页面每秒刷新进度，失败项可见，可单份或整批重试；任务号写进地址栏
   （`/grading?task=12`），刷新或从别处返回还能接上。

任务完成后可以跳到 **复核页**（`/review`）：

- “待人工复核”按风险从高到低列出需要确认的结论；点开某份，看逐条判定与采样分歧，
  点证据会在答卷原文里高亮对应片段；
- 可以直接**采纳原分**，也可以**改分**；改分必须写意见，定稿后原分与复核意见同时保留；
- “自动通过（待定稿）”页签支持勾选后**批量通过**，提交前会弹确认框。

```bash
npm run test        # Vitest（83 个用例，不需要浏览器）
npm run typecheck   # vue-tsc
npm run build       # 类型检查 + 生产构建
```

生产部署是后续工单：目前 `dist/` 需要由静态服务器托管，并保证 `/api` 反向代理到同一后端。

**导入一个班的答卷（先预览、再入库）**

```bash
# 预览：返回行数、题号、坏行（只报行号与原因）与重复项，不写库
curl -s -b /tmp/c.txt -X POST http://127.0.0.1:8000/api/answers/preview \
  -H 'content-type: application/json' \
  --data-binary @- <<'JSON'
{"source":"csv","text":"学号,题号,答案\n2024001,Q-01,客户端发送 SYN\n"}
JSON

# 确认入库：学号在解析阶段就丢弃，只生成 S-0001 这类脱敏编号
curl -s -b /tmp/c.txt -X POST http://127.0.0.1:8000/api/answers/import \
  -H 'content-type: application/json' \
  --data-binary @- <<'JSON'
{"source":"csv","text":"学号,题号,答案\n2024001,Q-01,客户端发送 SYN\n"}
JSON
```

入库要求题号已存在（保存评分标准时会创建题目）。样例文件见 `data/samples/tcp-handshake/answers.csv`。

**批量评分（后台任务 + 轮询进度）**

```bash
# 建任务：任务启动时固定当前标准，之后改标准不影响本批次
curl -s -b /tmp/c.txt -X POST http://127.0.0.1:8000/api/tasks \
  -H 'content-type: application/json' \
  -d '{"question_code":"Q-01","answer_ids":[2,3],"samples":1}'

# 查进度：每份答卷的状态、分数与路由（"待复核"由 route 派生）
curl -s -b /tmp/c.txt http://127.0.0.1:8000/api/tasks/1

# 只重试失败的那几份（空数组表示本任务全部失败项）
curl -s -b /tmp/c.txt -X POST http://127.0.0.1:8000/api/tasks/1/retry \
  -H 'content-type: application/json' -d '{"answer_ids":[]}'
```

单份失败不会中断整批：它会被标成 `failed` 并附上原因，其余照常评完。
任务跑在进程内线程池里（`GRADING_CONCURRENCY`，默认 2），进程重启后残留的 running 任务会被标为
`interrupted` 并可重试。

**复核与定稿（改分要写理由，定稿只发生一次）**

```bash
# 待复核队列：只有 route=human 的当前结论，risk 从高到低
curl -s -b /tmp/c.txt "http://127.0.0.1:8000/api/results?route=human"

# 详情：逐条判定、证据在原文中的起止位置、每次采样各自的等级
curl -s -b /tmp/c.txt http://127.0.0.1:8000/api/results/1

# 改分并定稿：原分不会被改写，复核记录里同时留下修改人与意见
curl -s -b /tmp/c.txt -X POST http://127.0.0.1:8000/api/results/1/review   -H 'content-type: application/json'   -d '{"decision":"override","final_score":6.5,"comment":"P2 的证据只覆盖半个要点"}'

# 批量通过自动路由的结果（含 route=human 的必须逐份看，混进来会整批拒绝）
curl -s -b /tmp/c.txt "http://127.0.0.1:8000/api/results?route=auto"
curl -s -b /tmp/c.txt -X POST http://127.0.0.1:8000/api/results/batch-accept   -H 'content-type: application/json' -d '{"aggregate_ids":[2,3]}'
```

一份结果只能定稿一次（重复提交 409）。定稿之后即使评分标准再改，这份结果与它的采分点描述也不会变；
已定稿的答卷也不能再进入新的评分任务或重新评分——更正需要撤销定稿，当前版本没有该入口。
任务列表里这份答卷的状态会变成 `finalized`，并同时给出 `final_score`（模型原分仍在 `score`）。

**先把教师的一段口述变成结构化标准**

```bash
cd backend
# 离线：用预置的模型输出，不花钱、不联网
python3 scripts/gen_rubric.py --stem "简述 TCP 三次握手的过程，并说明其目的。" --total 10 \
    --standard "三个要点：客户端发 SYN 给 3 分；服务器回 SYN+ACK 给 4 分；客户端回 ACK 给 3 分。" \
    --response ../data/samples/tcp-handshake/generation.json --save-rubric /tmp/rubric.json
```

它打印三份视图：**摘要**（这份标准在考什么）、**预览表**（有没有漏或重复）、
**校验清单**（权重和、等级依据）。校验清单分两类：**阻断保存**的结构错误与**仅提示**的语义问题。
模型漏了采分点时只会报"还有 N 分未分配"，不会替你补点。

**走接口：生成 → 保存 → 改一行 / 带意见重来**

```bash
# 1. 生成草稿（不落库）：返回摘要、预览表、校验清单与结构化标准
curl -s -b /tmp/c.txt -X POST http://127.0.0.1:8000/api/rubrics/generate \
  -H 'content-type: application/json' \
  -d '{"stem":"简述 TCP 三次握手的过程，并说明其目的。","total_score":10,"standard_text":"三个要点：客户端发 SYN 给 3 分；服务器回 SYN+ACK 给 4 分；客户端回 ACK 给 3 分。"}'

# 2. 确认后保存：整份提交；结构错误会被逐条拒绝（issues 里带 P1 / D1）
python3 -c 'import json;print(json.dumps({"rubric":json.load(open("data/samples/tcp-handshake/rubric.json")),"source_text":"三个要点……"}))' \
  | curl -s -b /tmp/c.txt -X POST http://127.0.0.1:8000/api/rubrics \
    -H 'content-type: application/json' --data-binary @-

# 3. 列表（每题只给最新一版）与按版本打开（含当时的教师原话与修改意见）
curl -s -b /tmp/c.txt http://127.0.0.1:8000/api/rubrics
curl -s -b /tmp/c.txt http://127.0.0.1:8000/api/rubrics/1

# 4. 行内编辑：改一行后整份提交，追加一版，旧版原样保留
python3 - <<'PY' > /tmp/edited-rubric.json
import json
rubric = json.load(open("data/samples/tcp-handshake/rubric.json"))
rubric["items"][0]["desc"] = "客户端发送 SYN，并给出初始序列号"
print(json.dumps({"rubric": rubric, "feedback": "P1 描述写具体"}))
PY
curl -s -b /tmp/c.txt -X PUT http://127.0.0.1:8000/api/rubrics/1 \
  -H 'content-type: application/json' --data-binary @/tmp/edited-rubric.json

# 5. 带上一版 + 一条意见重新生成：上一版整份进提示词，模型只该动被指出的部分
curl -s -b /tmp/c.txt -X POST http://127.0.0.1:8000/api/rubrics/1/iterate \
  -H 'content-type: application/json' -d '{"feedback":"P3 拆得太粗，拆成两个采分点"}'
```

迭代响应里的 `changes` 是服务端算出的差异清单（哪个点新增/删除/改了哪些字段），
用来看模型有没有顺手动了别处。确认后对响应里的 `based_on.rubric_id` 发 `PUT`
（而不是 `POST`）保存，新版本才会记下"改自哪一版"。

保存只追加版本，旧版永远能按 id 打开；已经建好的评分任务仍指向它启动时那一版。

**合成用例与对照实验**

```bash
cd backend
# 只校验用例，不调模型、不写库
python3 scripts/experiment.py check --rubric ../data/samples/tcp-handshake/rubric.json \
    --cases ../data/samples/tcp-handshake/cases.json

# 四组对照：B0 直接要总分 / B2 逐点 / B3 逐点+接地 / B5 采样+路由（真调模型，需要 --live）
python3 scripts/experiment.py run --rubric ../data/samples/tcp-handshake/rubric.json \
    --cases ../data/samples/tcp-handshake/cases.json --live --configs b0,b2,b3,b5 --samples 3
```

用例 = 答案 + 期望总分 + 每条采分点期望等级；每套标准至少要有一条 `correct=true` 的正确答案。
输出通过率、MAE、QWK、接地率与自动通过率，并列出逐条失败明细。QWK 按 0.5 分档；
样本不足或只有单一分数时显示 `—`（无定义），不写成 0。B5 **不含校准**，论文里不要写成完整 B5。

**跑一次评分并把结果落库**

```bash
cd backend
# 离线：用预置的模型输出，不花钱、不联网
python3 -m app.cli --rubric ../data/samples/tcp-handshake/rubric.json --answer ../data/samples/tcp-handshake/answer.txt \
    --response ../data/samples/tcp-handshake/response.json --samples 3 --save
```

输出里的 `stored.answer_id` / `run_ids` / `aggregate_id` 就是库里的主键，可以用 `Store.load_evaluation()`
原样读回：标准快照、答卷、每次采样、逐条证据与偏移、聚合指标、路由结论，一个字段都不少。

**10 分钟演示（答辩 / 验收）**

```bash
./scripts/demo.sh reset      # 起库 + 迁移 + 两份离线复核数据（会清空本地演示库）
```

完整流程、逐步输入与预期现象、模型故障时的降级路径见 **[docs/demo.md](docs/demo.md)**。
演示数据只有 `S-DEMO-*` 脱敏编号，导入文件不含学号列，不含真实姓名学号。

**跑测试**

```bash
cd backend
python3 -m unittest discover -s tests -t .              # 无需数据库，缺依赖时自动跳过存储用例
DEEPJUDGE_TEST_MYSQL_URL='mysql+pymysql://deepjudge:deepjudge@127.0.0.1:3307/deepjudge?charset=utf8mb4' \
  python3 -m unittest tests.test_store_mysql             # MySQL 真库：字符集与 emoji
# 该用例每次新建 deepjudge_test_<随机> 临时库、跑完删掉，不会动你给的库里的数据；
# 连接账号需要有 CREATE DATABASE 权限，没有就自动 skip

cd frontend
npm run test                                            # 前端：83 个用例，登录守卫/标准/导入/任务/复核
```
