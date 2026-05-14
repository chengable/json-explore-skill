#!/usr/bin/env python3
"""
JSON 渐进式探索工具 — 零依赖，专为超大 JSON 的渐进披露设计。

默认：递归展示所有 key 的树形结构 + 叶子值的前50字符预览 + 内容长度
钻入：--path 进入深层子树 / --full 完整输出 / --head --tail 掐头去尾
搜索：--find-key 按 key 名搜索 / --grep 按值内容搜索

示例:
  json_explore.py data.json                          # 全局概览
  json_explore.py data.json -p data.extra            # 进入 data.extra 子树
  json_explore.py data.json -p data.req --full       # 完整输出 data.req
  json_explore.py data.json -p data.req -H 200 -t 100      # 前后各截一段
  json_explore.py data.json --find-key stacktrace    # 搜索名为 stacktrace 的 key
  json_explore.py data.json --grep "污点执行"         # 搜索值中包含"污点执行"的叶子
  json_explore.py data.json --find-key type --grep "污点"  # 组合: key含type且值含污点
  json_explore.py data.json -p data.events -K code,type_str,displayarg  # 数组投影
"""

import argparse
import json
import re
import sys
from typing import Any, Optional, Tuple


# ── helpers ──────────────────────────────────────────────────────────

def type_label(v: Any) -> str:
    if v is None:       return "null"
    if isinstance(v, bool):  return "bool"
    if isinstance(v, int):   return "int"
    if isinstance(v, float): return "float"
    if isinstance(v, str):   return "string"
    if isinstance(v, list):  return f"array[{len(v)}]"
    if isinstance(v, dict):  return f"object[{len(v)}]"
    return type(v).__name__


def is_leaf(v: Any) -> bool:
    return not isinstance(v, (dict, list))


def truncate(s: str, n: int) -> str:
    """截断字符串，保留前 n 个字符。"""
    if len(s) <= n:
        return s
    return s[:n] + "…"


def try_parse_json(s: str) -> Optional[Any]:
    """如果字符串是合法 JSON（对象或数组），返回解析结果；否则返回 None。"""
    stripped = s.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


def deep_expand(data: Any) -> Any:
    """递归展开嵌套的 JSON 字符串。"""
    if isinstance(data, dict):
        return {k: deep_expand(v) for k, v in data.items()}
    if isinstance(data, list):
        return [deep_expand(item) for item in data]
    if isinstance(data, str):
        parsed = try_parse_json(data)
        if parsed is not None:
            return deep_expand(parsed)
    return data


# ── core: tree render ────────────────────────────────────────────────

def render_tree(
    data: Any,
    indent: int = 0,
    max_preview: int = 100,
    max_array_items: int = 10,
    path_prefix: str = "",
) -> list[str]:
    """递归渲染 JSON 树，返回行列表。"""
    lines: list[str] = []
    pad = "  " * indent

    if isinstance(data, dict):
        for k, v in data.items():
            cur = f"{path_prefix}.{k}" if path_prefix else k
            if is_leaf(v):
                lines.append(_leaf_line(pad, k, v, max_preview))
            else:
                lines.append(f"{pad}{k} → {type_label(v)}")
                lines.extend(render_tree(v, indent + 1, max_preview, max_array_items, cur))

    elif isinstance(data, list):
        if not data:
            lines.append(f"{pad}[] empty")
            return lines

        show = data[:max_array_items]
        rest = len(data) - max_array_items

        for i, item in enumerate(show):
            cur = f"{path_prefix}[{i}]"
            if is_leaf(item):
                lines.append(_leaf_line(pad, f"[{i}]", item, max_preview))
            else:
                lines.append(f"{pad}[{i}] → {type_label(item)}")
                lines.extend(render_tree(item, indent + 1, max_preview, max_array_items, cur))

        if rest > 0:
            # 统计剩余元素类型
            types = {type_label(data[j]).partition("[")[0] for j in range(max_array_items, min(len(data), max_array_items + 5))}
            lines.append(f"{pad}… [{max_array_items}..{len(data)-1}] {rest} more items, types: {', '.join(sorted(types))}")

    return lines


def _leaf_line(pad: str, key: str, value: Any, max_preview: int) -> str:
    raw = json.dumps(value, ensure_ascii=False)
    size = len(raw)
    preview = truncate(raw, max_preview)
    return f"{pad}{key} = {preview}  [{size} chars]"


# ── core: path resolution ────────────────────────────────────────────

def resolve_path(data: Any, path: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    解析点号路径，支持两种写法:
      data.extra[0].events
      data.extra.0.events
    """
    normalized = re.sub(r"\[(\d+)\]", r".\1", path)
    segments = [s for s in normalized.split(".") if s]

    cur = data
    for seg in segments:
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError) as e:
                return None, f"数组索引 '{seg}' 无效: {e}"
        elif isinstance(cur, dict):
            if seg not in cur:
                avail = list(cur.keys())[:15]
                hint = ", ".join(repr(k) for k in avail)
                return None, f"键 '{seg}' 不存在。可用: [{hint}{'…' if len(cur) > 15 else ''}]"
            cur = cur[seg]
        else:
            return None, f"无法在 {type(cur).__name__} 上访问 '{seg}'"
    return cur, None


# ── core: search ──────────────────────────────────────────────────────

def find_keys(
    data: Any,
    pattern: str,
    path_prefix: str = "",
    case_sensitive: bool = False,
) -> list[dict]:
    """递归搜索 key 名中包含 pattern 的位置。返回 [{path, type, preview}]。"""
    results: list[dict] = []
    _match = lambda s: pattern in s if case_sensitive else pattern.lower() in s.lower()

    def _walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                cur = f"{prefix}.{k}" if prefix else k
                if _match(k):
                    results.append({
                        "path": cur,
                        "type": type_label(v),
                        "preview": truncate(json.dumps(v, ensure_ascii=False), 100) if is_leaf(v) else "",
                    })
                if not is_leaf(v):
                    _walk(v, cur)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                cur = f"{prefix}[{i}]"
                _walk(item, cur)

    _walk(data, path_prefix if path_prefix else "")
    return results


def grep_values(
    data: Any,
    pattern: str,
    path_prefix: str = "",
    case_sensitive: bool = False,
) -> list[dict]:
    """递归搜索叶子值中包含 pattern 的位置。返回 [{path, type, preview, size}]。"""
    results: list[dict] = []
    _match = lambda s: pattern in s if case_sensitive else pattern.lower() in s.lower()

    def _walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                cur = f"{prefix}.{k}" if prefix else k
                _walk(v, cur)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                cur = f"{prefix}[{i}]"
                _walk(item, cur)
        else:
            raw = json.dumps(node, ensure_ascii=False)
            if _match(raw):
                results.append({
                    "path": prefix,
                    "type": type_label(node),
                    "preview": truncate(raw, 120),
                    "size": len(raw),
                })

    _walk(data, path_prefix if path_prefix else "")
    return results


# ── core: array projection ───────────────────────────────────────────

def project_array(data: list, keys: list[str], max_preview: int, max_items: int) -> str:
    """对于对象数组，只展示每个元素中指定的 key，用于横向对比。"""
    lines: list[str] = []
    show = data[:max_items]
    rest = len(data) - max_items

    for i, item in enumerate(show):
        lines.append(f"── [{i}] ──")
        if not isinstance(item, dict):
            raw = json.dumps(item, ensure_ascii=False)
            lines.append(f"  (value) = {truncate(raw, max_preview)}  [{len(raw)} chars]")
            continue
        for k in keys:
            if k in item:
                v = item[k]
                if is_leaf(v):
                    raw = json.dumps(v, ensure_ascii=False)
                    lines.append(f"  {k}: {truncate(raw, max_preview)}  [{len(raw)} chars]")
                else:
                    lines.append(f"  {k} → {type_label(v)}")
            else:
                lines.append(f"  {k}: (missing)")

    if rest > 0:
        lines.append(f"… [{max_items}..{len(data)-1}] {rest} more items")
    return "\n".join(lines)


# ── core: value display ──────────────────────────────────────────────

def display_value(value: Any, head: Optional[int], tail: Optional[int], full: bool) -> str:
    """以 head / tail / full 控制输出值的内容。"""
    raw = json.dumps(value, ensure_ascii=False, indent=2)
    total = len(raw)

    if full:
        return raw

    if head is not None and tail is not None:
        if total <= head + tail:
            return raw
        return f"{raw[:head]}\n… [{total - head - tail} chars omitted] …\n{raw[-tail:]}"

    if head is not None:
        if total <= head:
            return raw
        return f"{raw[:head]}\n… [{total - head} more chars]"

    if tail is not None:
        if total <= tail:
            return raw
        return f"… [{total - tail} chars before] …\n{raw[-tail:]}"

    # default
    return truncate(raw, 100) + (f"\n… [{total - 100} more chars]" if total > 100 else "")


# ── main ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="JSON 渐进式探索 — 默认展示 key 树 + 叶子值大小与预览",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s data.json                       # 全局树形概览
  %(prog)s data.json -p data.extra         # 钻入子树
  %(prog)s data.json -p data.req --full    # 完整输出叶子值
  %(prog)s data.json -p data.req -H 200 -t 100   # 前200+后100字符
  %(prog)s data.json -m 30 -a 3            # 收窄预览: 30字符, 数组只展开3项
  %(prog)s data.json --find-key stacktrace # 搜索 key 名
  %(prog)s data.json --grep "污点执行"      # 搜索值内容
  %(prog)s data.json --find-key type --grep "污点"  # 组合搜索
  %(prog)s data.json -p data.events -K code,type_str,displayarg  # 数组投影
        """,
    )
    parser.add_argument("file", help="JSON 文件路径")
    parser.add_argument("--path", "-p", help="钻入路径，如 data.extra[0].events")
    parser.add_argument("--max", "-m", type=int, default=50, help="叶子值预览截断长度 (默认 50)")
    parser.add_argument("--head", "-H", type=int, help="输出前 N 个字符")
    parser.add_argument("--tail", "-t", type=int, help="输出后 N 个字符")
    parser.add_argument("--full", "-f", action="store_true", help="完整输出 (配合 --path)")
    parser.add_argument("--array", "-a", type=int, default=5, help="数组展开上限 (默认 5)")
    parser.add_argument("--find-key", "-k", help="搜索 key 名中包含该字符串的所有路径")
    parser.add_argument("--grep", "-g", help="搜索叶子值中包含该字符串的所有路径")
    parser.add_argument("--case-sensitive", "-c", action="store_true", help="搜索区分大小写")
    parser.add_argument("--limit", "-l", type=int, default=50, help="搜索结果上限 (默认 50)")
    parser.add_argument("--keys", "-K", help="数组投影：只展示每个元素的指定字段，逗号分隔，如 'code,type_str,displayarg'")

    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as fh:
        try:
            data = deep_expand(json.load(fh))
        except json.JSONDecodeError as e:
            print(
                f"该文件不是合法的 JSON，json_explore 无法解析。\n"
                f"原因: {e}\n"
                f"\n"
                f"请根据文件实际类型渐进式浏览，不要一次性读取全部内容：\n"
                f"  纯文本:  head -100 / tail -100 / wc -c 查看大小和首尾\n"
                f"  YAML:    python3 -c \"import yaml; yaml.safe_load(open('{args.file}'))\" | json_explore\n"
                f"  XML:     xmllint --format 或 python3 -c \"import xmltodict\" 转 JSON 后管道传入\n"
                f"  NDJSON:  每行一个 JSON，用 head -n 3 先看前几行结构\n"
                f"  二进制:  file {args.file} 确认类型，再选对应工具",
                file=sys.stderr,
            )
            sys.exit(1)

    # 确定作用域
    scope = data
    scope_label = "(root)"
    if args.path:
        scope, err = resolve_path(data, args.path)
        if err:
            print(f"Error: {err}", file=sys.stderr)
            sys.exit(1)
        scope_label = args.path

    # ── 数组投影模式 ──
    if args.keys and isinstance(scope, list):
        keys = [k.strip() for k in args.keys.split(",")]
        print(f"# {scope_label} → {type_label(scope)}, keys: {keys}")
        print(project_array(scope, keys, args.max, args.array))
        return

    # ── 搜索模式 ──
    if args.find_key or args.grep:
        key_hits: list[dict] = []
        val_hits: list[dict] = []

        if args.find_key:
            key_hits = find_keys(scope, args.find_key, scope_label if args.path else "", args.case_sensitive)
        if args.grep:
            val_hits = grep_values(scope, args.grep, scope_label if args.path else "", args.case_sensitive)

        # 组合搜索：取交集（按路径）
        if args.find_key and args.grep:
            key_paths = {h["path"] for h in key_hits}
            val_hits = [h for h in val_hits if h["path"] in key_paths]
            # 对于匹配 value 的路径，也要确保其 key 匹配（路径最后一段）
            final: list[dict] = []
            for h in val_hits:
                leaf_key = h["path"].rsplit(".", 1)[-1] if "." in h["path"] else h["path"]
                if args.find_key.lower() in leaf_key.lower() if not args.case_sensitive else args.find_key in leaf_key:
                    final.append(h)
            val_hits = final
            key_hits = []  # 组合模式只展示值匹配

        # 输出 key 搜索结果
        if key_hits:
            total = len(key_hits)
            shown = key_hits[: args.limit]
            print(f"# --find-key '{args.find_key}'  in {scope_label} → {total} hits")
            for h in shown:
                preview = f" | {h['preview']}" if h["preview"] else ""
                print(f"  {h['path']}  [{h['type']}]{preview}")
            if total > args.limit:
                print(f"  … {total - args.limit} more hits (use --limit to show more)")

        # 输出值搜索结果
        if val_hits:
            total = len(val_hits)
            shown = val_hits[: args.limit]
            print(f"# --grep '{args.grep}'  in {scope_label} → {total} hits")
            for h in shown:
                print(f"  {h['path']}  [{h['type']}] ({h['size']} chars)")
                print(f"      {h['preview']}")
            if total > args.limit:
                print(f"  … {total - args.limit} more hits (use --limit to show more)")

        return

    # ── 展示模式 ──
    if args.path and (is_leaf(scope) or args.full or args.head or args.tail):
        print(display_value(scope, args.head, args.tail, args.full))
    else:
        if args.path:
            print(f"# {args.path} → {type_label(scope)}")
        print("\n".join(render_tree(scope, max_preview=args.max, max_array_items=args.array)))


if __name__ == "__main__":
    main()
