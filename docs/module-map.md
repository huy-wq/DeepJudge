# 模块图与数据流

> 可视化版本（推荐先看）：[module-map.html](module-map.html) —— 浏览器直接打开，含数据流卡片与分层模块图。
> 本文是文字速览，方便在 GitHub 上读和检索。

## 一、llmgrader 的一次评分：9 步

| # | 步骤 | 数据形态 | 关键点 |
| --- | --- | --- | --- |
| 1 | 讲师写课程包 | 磁盘 XML | 题面用 CDATA 包 HTML，公式用 LaTeX |
| 2 | `unit_parser` 校验解析 | 内存 dict | `xmlschema` 按 `unit.xsd` 校验 |
| 3 | 学生提交 | HTTP JSON + 图片 | 匿名 session UUID，无身份信息 |
| 4 | `PromptBuilder` 拼提示词 | 纯文本 | 按「二值/部分 × 单/多部分 × 有无 rubric」选模板 |
| 5 | 调模型 | HTTP | Responses API，`json_object`；唯一不确定环节 |
| 6 | 解析 + 宽松校验 | pydantic 对象 | `GraderRawResult`，脏条目丢弃而非整单失败 |
| 7 | **后端派生分数** | `GradeResult` | 算 max/聚合/clamp；模型无权给总分 |
| 8 | 落库 | SQLite 行 | 答案、rubric、逐条证据、token、时延 |
| 9 | 出口 | 页面 / zip | 网页逐条展示；Gradescope 包离线判分 |

**一句话**：模型只在第 5 步出现，产出一段 JSON；分数是第 7 步由普通 Python 算出来的。

## 二、五层模块

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 入口 | `routes/api.py`（17 个接口）、`templates/`、`static/app.js` | 接收提交、返回结果、前端排版（MathJax） |
| 服务 | `grader.py`、`prompt.py`、`unit_parser.py`、`gradetests.py`、`portal_storage.py` | 编排、拼词、解析、回归测试、持久化 |
| 领域 | `GradeResult`、`GraderRawResult`、`RubricEvalItem`、`ModelSpec`、`Question/Part/RubricItem` | 数据结构与约束 |
| 适配 | OpenAI Responses API、sqlite3、xmlschema、文件系统 | 唯一的外部出口 |
| 离线 | MCP Course Builder、`build_autograder.py`、`llmgrader_test`、`create_soln_pkg.py` | 准备阶段工具，不在请求链路里 |

## 三、XML 涉及的标准

| 标准 | 用在哪 | 说明 |
| --- | --- | --- |
| XML 1.0 | 课程包 | UTF-8 声明 |
| XSD 1.0 | `schemas/` 下 6 个 schema | `xmlschema` 校验；**无 targetNamespace**；枚举写死 |
| CDATA | 题面、参考解、测试用例 | 内嵌 HTML 不转义；不能含 `]]>` |
| HTML 子集 | 题目/解析渲染 | 浏览器直接渲染 |
| LaTeX + MathJax | 公式 | 行内 `\( \)`、行间 `\[ \]`，CDN 加载 |
| JSON | 模型输出、接口 | `json_object` |
| JSON Schema（pydantic） | 输出校验 | `extra="forbid"` + `Literal` 约束 |
| Gradescope Autograder 规范 | 导出包 | `run_autograder` + `results.json` |
| MCP（JSON-RPC 2.0） | Course Builder | 编辑器 agent 读写课程包 |

## 四、换到 DeepJudge

同一条流，多三步：**多采样聚合**、**证据接地校验**、**校准与路由**。模块对照见
[module-map.html](module-map.html) 第 5 节，接口细节见 [architecture.md](architecture.md) 第 3 节，
参考项目取舍见 [reference-llmgrader.md](reference-llmgrader.md)。
