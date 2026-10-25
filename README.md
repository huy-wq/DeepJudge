# DeepJudge · 基于 DeepSeek 的主观试题评判研究与设计

> 课题代码仓库。目标：研究并实现一个**可解释、可复现、可追责**的主观题自动评判系统。

## 一、课题要解决的问题

主观题（简答、论述、案例分析、计算过程、编程题）自动评分的难点不在"让大模型打个分"——那只要一段提示词。
真正的难点是下面三个，也是本课题的研究落点：

| 问题 | 具体表现 | 本课题的应对 |
| --- | --- | --- |
| **一致性** | 同一份答卷重复评分波动 1~3 分，无法作为正式成绩 | 采分点级结构化评分 + 多次采样聚合 + 分数校准 |
| **可解释** | 只给一个总分，教师不认可、学生不服、无法申诉 | 评分标准结构化 + 逐点判定 + 证据原文定位 |
| **可追责** | 模型编造判分理由，出错后无法定位是哪一步出错 | 证据接地校验 + 全链路版本化 + 审计日志 |

## 二、核心思路（一句话版）

> 把"让模型给一个总分"改造成"**让模型逐条判定采分点是否命中，并为每条判定提供原文证据**"，
> 再用多次采样的一致性作为置信度，对低置信结果路由人工复核，最后用人工标注样本做分数校准。

详细设计见 [docs/architecture.md](docs/architecture.md)，实验与评测方案见 [docs/evaluation.md](docs/evaluation.md)。

## 三、仓库结构

```text
DeepJudge/
├── README.md                 # 本文件：课题定位与导航
├── .env.example              # 环境变量模板（真实 .env 不入库）
├── .gitignore
├── docs/
│   ├── architecture.md       # 核心技术方案：评分流水线、数据模型、工程要点
│   └── evaluation.md         # 评测方案：数据集、指标、消融实验矩阵
├── backend/                  # 服务端（技术栈待定，见下）
├── frontend/                 # 评卷与复核界面
└── data/                     # 本地数据（答卷、标注集），默认不入库
```

## 四、技术栈

> 状态：**待确认**。当前推荐 Python 生态，理由是 NLP/评测/统计（QWK、isotonic 校准、bootstrap）依赖成熟，
> 且 DeepSeek SDK 与 openai 兼容。

- 后端：FastAPI + SQLAlchemy + SQLite（开发）/ PostgreSQL（部署）
- 异步任务：Redis + RQ / Celery（评分是长任务，必须异步 + 可重试）
- 前端：Vue 3 + Element Plus（评卷台、复核台、统计看板）
- 模型：DeepSeek API，`deepseek-flash` 为主，`deepseek-v4-pro` 用于对照实验
- 部署：Docker Compose + Nginx

## 五、模型与 API 现状（2026-10 核实）

| 项 | 值 |
| --- | --- |
| 模型 | `deepseek-flash`（V4.1-Flash-1017）、`deepseek-v4-pro`（V4-Pro-0813） |
| 上下文 / 输出 | 1M / 最大 384K |
| 思考模式 | 默认开启，`reasoning_effort` = low/high/max；**思考模式下 `temperature` 不生效** |
| 结构化输出 | `response_format={"type":"json_object"}`，偶发空响应需重试 |
| 工具调用 | 支持（可做函数式评分骨架） |
| 价格（flash） | 缓存命中输入 0.02~0.04 元/M，未命中 1~2 元/M，输出 4~8 元/M（空闲时段半价） |

关键推论：**思考模式下无法用 temperature=0 换取确定性**，稳定性必须靠"多次采样 + 聚合 + 校准"来获得，
这恰好构成本课题的实验变量之一。同时 rubric 等稳定前缀可利用上下文硬盘缓存，输入成本可降一个数量级。

## 六、快速开始

```bash
git clone <your-remote-url> DeepJudge && cd DeepJudge
cp .env.example .env          # 填入 DEEPSEEK_API_KEY
python3 -m venv .venv && source .venv/bin/activate
# 依赖与服务启动命令待技术栈确认后补充
```

## 七、进度

- [x] 仓库初始化、方案与评测设计文档
- [ ] 技术栈确认与工程骨架
- [ ] 评分引擎 MVP（单题、单评分标准、结构化输出）
- [ ] 标注数据集与基线实验
- [ ] 校准、人机协同与复核界面
- [ ] 消融实验与论文撰写
