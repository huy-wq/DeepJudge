# 本机工具配置记录

## Matt Pocock skill 包

| 项 | 值 |
| --- | --- |
| 来源 | https://github.com/mattpocock/skills |
| 版本 | commit `5beb35b040e3de97e54fa9e386d7e42d15c295c2`（2026-10-19） |
| 许可 | MIT |
| 安装位置 | `.dsh/skills/`（本项目级，已被 `.gitignore` 忽略） |
| 数量 | 38 个 skill，其中 15 个对模型可见，23 个仅用户主动调用 |

### 为什么是拍平的目录结构

DSH 的 skill 发现规则（`@deepseek-ai/dsh-skill-filesystem`）：

- 扫描根：`<项目>/.dsh/skills`（rank 100）、`<项目>/.agents/skills`（rank 200）、
  `~/.dsh/skills`（rank 400）、`~/.agents/skills`（rank 500）
- **只认根目录下一层**：`<root>/<name>/SKILL.md` 或 `<root>/<name>.md`，不递归
- frontmatter 必须有 `name`（kebab-case）与 `description`

上游仓库按分类嵌套（`skills/engineering/tdd/SKILL.md`），直接整棵树拷进去不会被识别，
因此安装时把每个 skill 目录拍平到 `.dsh/skills/<name>/`，并保留其 `agents/`、`scripts/`
等附随资源（skill 正文会引用这些文件）。

### 更新

```bash
rm -rf /tmp/mp-skills
git clone --depth 1 https://github.com/mattpocock/skills /tmp/mp-skills
rm -rf .dsh/skills
mkdir -p .dsh/skills
for f in $(find /tmp/mp-skills/skills -name SKILL.md); do
  cp -r "$(dirname "$f")" ".dsh/skills/$(basename "$(dirname "$f")")"
done
```

拍平后可用如下命令校验 frontmatter 与目录名一致：

```bash
cd .dsh/skills && for d in */; do n=${d%/}
  name=$(awk -F': ' '/^name:/{print $2; exit}' "$n/SKILL.md")
  [ "$name" = "$n" ] || echo "mismatch: $n"
done
```

### 全局安装（所有项目可用）

```bash
mkdir -p ~/.dsh/skills
cp -r .dsh/skills/* ~/.dsh/skills/
```

`~/.dsh/skills` 在 rank 400，项目级同名 skill 会覆盖全局版本。
