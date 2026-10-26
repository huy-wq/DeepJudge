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
│   ├── roadmap.md       # 功能实现清单（顺序与依赖）
│   ├── ticket-notes.md  # 工单问题记录（遇到的坑与未决问题）
│   ├── experiments.md   # 实验记录（真实调用的设置与结果）
│   ├── agents/          # issue tracker / 标签 / 领域文档约定
│   └── tooling.md       # 本地工具配置
├── backend/             # Python + FastAPI 评分引擎（结构见架构文档第 3 节）
│   ├── migrations/      # Alembic 迁移（表结构的部署真源）
│   └── tests/           # 单测与真库验证
├── scripts/dev-db.sh    # 本地数据库一键起库 + 升级表结构
├── docker-compose.yml   # 本地 MySQL 8（utf8mb4）
├── frontend/            # 复核界面（选型待定，目录待创建）
└── data/                # 本地答卷与标注集，默认不入库
```

## 四、技术栈

| 层 | 选型 | 状态 |
| --- | --- | --- |
| 语言 / 运行时 | Python 3.10+ | **已定** |
| Web 框架 | FastAPI | **已定** |
| 服务形态 | 单体后端 | **已定** |
| 存储 | MySQL 8（`utf8mb4`）+ Alembic 迁移 | **已实现** |
| 任务执行 | 进程内后台任务 / 独立 worker | 待定 |
| 前端 | Vue 3 | **已定**（组件库待定） |
| LLM 供应商与参数 | DeepSeek（`providers/deepseek.py`，标准库实现） | **已接入** |
| 部署 | 本地优先 | 待定 |

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

**跑一次评分并把结果落库**

```bash
cd backend
# 离线：用预置的模型输出，不花钱、不联网
python3 -m app.cli --rubric ../data/samples/tcp-handshake/rubric.json --answer ../data/samples/tcp-handshake/answer.txt \
    --response ../data/samples/tcp-handshake/response.json --samples 3 --save
```

输出里的 `stored.answer_id` / `run_ids` / `aggregate_id` 就是库里的主键，可以用 `Store.load_evaluation()`
原样读回：标准快照、答卷、每次采样、逐条证据与偏移、聚合指标、路由结论，一个字段都不少。

**跑测试**

```bash
cd backend
python3 -m unittest discover -s tests -t .              # 无需数据库，缺依赖时自动跳过存储用例
DEEPJUDGE_TEST_MYSQL_URL='mysql+pymysql://deepjudge:deepjudge@127.0.0.1:3307/deepjudge?charset=utf8mb4' \
  python3 -m unittest tests.test_store_mysql             # MySQL 真库：字符集与 emoji
# 该用例每次新建 deepjudge_test_<随机> 临时库、跑完删掉，不会动你给的库里的数据；
# 连接账号需要有 CREATE DATABASE 权限，没有就自动 skip
```
