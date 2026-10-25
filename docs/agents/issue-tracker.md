# Issue 追踪：GitHub

本仓库的 issue 与 spec 以 GitHub issue 的形式存在。所有操作统一通过 `gh` CLI 完成。

> **前置条件**：远程仓库已就绪（`origin` = `git@github.com:huy-wq/DeepJudge.git`），但本机尚未安装 `gh`。
> 装好并登录 `gh` 之前，`to-spec`、`to-tickets`、`triage` 无法真正发布或读取 issue。

## 约定

- **创建 issue**：`gh issue create --title "..." --body "..."`。多行正文用 heredoc。
- **读取 issue**：`gh issue view <number> --json number,title,body,labels,comments`。
- **列出 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，配合相应的 `--label` 与 `--state` 过滤。
- **设为某 issue 的子 issue**：`gh issue create --parent <parent> ...`，或事后 `gh issue edit <parent> --add-sub-issue <child>`（需 `gh` 2.94+）。旧版 `gh` 用 `gh api --method POST repos/<owner>/<repo>/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`（数据库 id，见下文 **阻塞关系**）。若环境不支持子 issue，就在子 issue 正文顶部写上 `Part of #<parent>`。
- **评论 issue**：`gh issue comment <number> --body "..."`
- **增删标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`

仓库由 `git remote -v` 推断；在克隆目录内执行时 `gh` 会自动识别。

## PR 作为分诊入口

**PR 作为请求入口：否。** _（若本仓库把外部 PR 当作需求入口，改为 `yes`；`/triage` 会读取此开关。）_

设为 `yes` 时，PR 与 issue 走同一套标签与状态流，改用对应的 `gh pr` 命令：

- **读取 PR**：`gh pr view <number> --comments`，以及 `gh pr diff <number>` 看差异。
- **列出待分诊的外部 PR**：`gh api --paginate 'repos/{owner}/{repo}/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`。
- **评论 / 打标签 / 关闭**：`gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub 的 issue 与 PR 共用一个编号空间，所以裸 `#42` 可能是两者之一：先用 `gh pr view 42` 解析，失败再回退 `gh issue view 42`。

## 当 skill 说「发布到 issue tracker」

创建一条 GitHub issue。

## 当 skill 说「获取相关 ticket」

按上文 **读取 issue** 执行。

## Wayfinder 操作

供 `/wayfinder` 使用。**地图**是一个 issue，**子 issue** 即票。

- **地图**：一条带 `wayfinder:map` 标签的 issue，正文承载 Notes / Decisions-so-far / Fog 三部分。`gh issue create --label wayfinder:map`。
- **子票**：作为地图的 GitHub 子 issue 关联（见 **设为某 issue 的子 issue**）。环境不支持子 issue 时，把子票加进地图正文的任务列表，并在子票正文顶部写 `Part of #<map>`。标签为 `wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。一旦认领，就把票分配给推进的开发。
- **阻塞关系**：用 GitHub 原生 issue 依赖，这是规范且 UI 可见的表达。加边：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，其中 `<blocker-db-id>` 是阻塞方的**数据库 id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，**不是** `#number`，也不是 `node_id`）。GitHub 通过 `issue_dependencies_summary.blocked_by` 报告仍在阻塞的依赖（只计未关闭的，即实时的闸门）。环境不支持依赖时，退回在子票正文顶部写 `Blocked by: #<n>, #<n>`。当所有阻塞方关闭后，该票即为解除阻塞。
- **前沿查询**：列出地图下所有未关闭子票（`gh issue list --state open`，限定为地图的子 issue 或任务列表），剔除仍有未关闭阻塞方（`issue_dependencies_summary.blocked_by > 0`，或 `Blocked by` 行中存在未关闭 issue）或已有 assignee 的，按地图顺序取第一个。
- **认领**：`gh issue edit <n> --add-assignee @me`，这是本会话的第一次写入。
- **解决**：`gh issue comment <n> --body "<结论>"`，然后 `gh issue close <n>`，再把一条上下文指针（要点 + 链接）追加到地图的 Decisions-so-far。
