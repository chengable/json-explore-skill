# json-explore

渐进式大 JSON 探索技能，解决 LLM Agent 上下文窗口被大 JSON 一次性撑满的问题。

## 解决什么问题

当 MCP 工具返回大 JSON（超过 `MAX_MCP_OUTPUT_TOKENS` 阈值），Claude Code 会将内容写入临时文件，Agent 拿到文件路径而非内容。默认行为是直接 `Read` 整个文件，几十万 token 瞬间塞满上下文，后续对话质量严重下降。

`json-explore` 提供零依赖 Python 脚本 + Skill 提示词，引导模型对 JSON 进行**渐进式探索**：先看结构概览 → 按需搜索定位 → 钻入子树 → 获取完整值。每一步只消耗必要的 token。

## 快速开始

```bash
# 结构概览 — 展示 key 树 + 叶子值前 50 字符 + 内容长度
python3 json_explore.py data.json

# 收窄预览 — 值截断 30 字符，数组只展开 3 项
python3 json_explore.py data.json -m 30 -a 3

# 搜索 key 名
python3 json_explore.py data.json --find-key stacktrace

# 搜索值内容
python3 json_explore.py data.json --grep "NullPointerException"

# 组合搜索 — key 名包含 type 且值包含 error
python3 json_explore.py data.json --find-key type --grep "error"

# 钻入子树
python3 json_explore.py data.json -p data.events

# 数组投影 — 只展示每个元素的指定字段，便于横向对比
python3 json_explore.py data.json -p data.events -K code,type,message

# 完整输出叶子值
python3 json_explore.py data.json -p data.events[0].stacktrace --full

# 掐头去尾 — 看开头 200 + 结尾 100 字符
python3 json_explore.py data.json -p data.req -H 200 -t 100
```

## 典型工作流

```
第1次：默认概览 → 了解顶层结构，找到感兴趣的区域
第2次：--find-key / --grep / -p 数组路径 -K 字段列表 → 精确定位
第3次：-p '路径' --full → 获取完整值
```

## 安装

1. 将 `json-explore/` 目录复制到 `.claude/skills/`（项目级或用户级 `~/.claude/skills/`）：

```bash
# 项目级
cp -r json-explore /your-project/.claude/skills/

# 用户级（所有项目生效）
cp -r json-explore ~/.claude/skills/
```

2. （推荐）在 `settings.json` 中降低 MCP 输出阈值：

```json
{
  "MAX_MCP_OUTPUT_TOKENS": "10000"
}
```

3. （推荐）在全局提示词中添加：

> MCP 返回值如果被写入文件，使用 json-explore 技能渐进探索，禁止一次性 Read 大文件。

## 特性

- **零依赖** — 仅需 Python 3 标准库
- **嵌套 JSON 自动展开** — MCP 返回中 `text` 字段是 JSON 字符串时，自动递归解析
- **非 JSON 文件友好提示** — 检测到非 JSON 格式时，输出对应工具建议（head/tail 等）
- **路径兼容** — `--find-key` / `--grep` 输出的路径可直接作为 `--path` 参数使用

## 适用场景

- MCP 工具返回超大的 JSON 响应
- 日志分析、API 响应调试、数据探索
- 任何需要"先看结构再深入"的大文件场景
