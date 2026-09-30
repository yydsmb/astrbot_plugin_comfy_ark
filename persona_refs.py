"""人格参考图的纯逻辑层 —— 零 astrbot 依赖。

用法约定（用户提出的形态）：
  「参考图需要用户提前放置在插件文件夹，以 AI 人格名称命名」

所以：插件目录下的 `refs/` 里放 `橙橘.png`、`小深.jpg` 这样的文件。
生成时插件按当前会话的**人格名**去找对应文件，找到就当参考图，
找不到就退化成纯文生图（或改用消息里附带的图）。

匹配顺序（越靠前越权威）：
  1. 别名映射（配置项 `reference_alias_map`，一行一条 `人格名=文件名`）
     —— 给「文件名没法用中文/名字对不上」的情况留后路；
  2. 文件名主干完全相同（忽略大小写）；
  3. 去掉空格、下划线、连字符、括号后的主干相同（`小 深` == `小深`）。

同名多格式（`橙橘.png` 与 `橙橘.jpg` 同时存在）按 IMAGE_EXT_ORDER 取优先级最高的，
保证结果稳定、不随目录遍历顺序漂移。
"""

from __future__ import annotations

import difflib
import os
import re
from typing import Any

# 支持的图片扩展名（按优先级排列）
IMAGE_EXT_ORDER = (".png", ".webp", ".jpg", ".jpeg", ".bmp", ".gif", ".avif")

# 用来做「宽松比对」时忽略掉的字符
LOOSE_STRIP_RE = re.compile(r"[\s_\-・·（）()\[\]【】]+")

# 文件名里不允许出现的字符（Windows + 跨平台）
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_name(name: Any) -> str:
    """把人格名整理成安全的文件名主干（挡 `../` 这类路径穿越）。"""
    text = str(name or "").strip()
    text = text.replace("..", "")
    text = _ILLEGAL_RE.sub("", text)
    return text.strip().strip(".")


def is_image_file(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in IMAGE_EXT_ORDER


def _ext_rank(path: str) -> int:
    ext = os.path.splitext(str(path or ""))[1].lower()
    try:
        return IMAGE_EXT_ORDER.index(ext)
    except ValueError:
        return len(IMAGE_EXT_ORDER)


def parse_alias_map(raw: Any) -> dict[str, str]:
    """解析 `reference_alias_map`：一行一条 `人格名=文件名`。

    分隔符容忍 `=`、`＝`、`:`、`：`；`#` 与 `//` 开头的行是注释。
    """
    out: dict[str, str] = {}
    text = str(raw or "")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        parts = re.split(r"[=＝:：]", line, maxsplit=1)
        if len(parts) != 2:
            continue
        key = parts[0].strip().lower()
        value = parts[1].strip().strip("\"'")
        if key and value:
            out[key] = value
    return out


def scan_ref_dir(dir_path: Any) -> dict[str, str]:
    """扫描参考图目录，返回 {规范化主干: 完整路径}。

    目录不存在 / 读不了时返回空字典（不抛异常 —— 参考图是可选能力，
    没放图不该让插件起不来）。
    """
    out: dict[str, str] = {}
    path = str(dir_path or "").strip()
    if not path or not os.path.isdir(path):
        return out
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return out

    for name in names:
        full = os.path.join(path, name)
        if not os.path.isfile(full) or not is_image_file(full):
            continue
        stem = os.path.splitext(name)[0]
        for key in _keys_of(stem):
            prev = out.get(key)
            if prev is None or _ext_rank(full) < _ext_rank(prev):
                out[key] = full
    return out


def _keys_of(stem: str) -> list[str]:
    """一个主干可以匹配多个键：原样小写、去分隔符、去空白。"""
    keys = []
    low = str(stem or "").strip().lower()
    if low:
        keys.append(low)
    loose = LOOSE_STRIP_RE.sub("", low)
    if loose and loose not in keys:
        keys.append(loose)
    return keys


def list_ref_files(dir_path: Any) -> list[tuple[str, str]]:
    """列出目录里所有参考图，返回 [(文件名主干, 完整路径)]，按文件名排序。"""
    path = str(dir_path or "").strip()
    if not path or not os.path.isdir(path):
        return []
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return []
    out: list[tuple[str, str]] = []
    for name in names:
        full = os.path.join(path, name)
        if os.path.isfile(full) and is_image_file(full):
            out.append((os.path.splitext(name)[0], full))
    return out


def resolve_ref(
    dir_path: Any,
    persona_name: Any,
    aliases: dict[str, str] | None = None,
    scan: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    """按人格名找参考图，返回 (完整路径 or None, 命中方式说明)。"""
    table = scan if scan is not None else scan_ref_dir(dir_path)
    if not table:
        return None, "参考图目录里一张图都没有"

    name = str(persona_name or "").strip()
    if not name:
        return None, "没拿到人格名（会话没绑人格，且没配 persona_name_default）"

    # 1) 别名映射
    if aliases:
        mapped = aliases.get(name.lower())
        if mapped:
            target = mapped.strip()
            # 允许写文件名（含扩展名）或主干
            for key in _keys_of(os.path.splitext(target)[0]):
                hit = table.get(key)
                if hit:
                    return hit, f"别名映射「{name} → {os.path.basename(hit)}」"
            direct = os.path.join(str(dir_path or ""), target)
            if os.path.isfile(direct) and is_image_file(direct):
                return direct, f"别名映射「{name} → {target}」"

    # 2) 主干完全相同 / 3) 宽松比对
    loose_name = LOOSE_STRIP_RE.sub("", name.lower())
    for key in _keys_of(name):
        hit = table.get(key)
        if hit:
            how = "文件名同名" if key == name.strip().lower() else "文件名近似匹配"
            return hit, f"{how}「{os.path.basename(hit)}」"

    if loose_name:
        for key, hit in table.items():
            if LOOSE_STRIP_RE.sub("", key) == loose_name:
                return hit, f"文件名近似匹配「{os.path.basename(hit)}」"
    return None, (
        f"目录里没有叫「{name}」或名字接近的图片"
        f"（目录现有 {len(table)} 张；可用 reference_alias_map 写「{name}=文件名」做映射）"
    )


def suggest_ref(
    dir_path: Any,
    persona_name: Any,
    limit: int = 3,
) -> list[tuple[str, float]]:
    """当前人格没有精确匹配时，按文件名相似度给出候选参考图。

    为什么需要它：参考图是按「人格名 = 文件名」硬匹配的，用户改了人格名
    或手抖写了个别名，插件就静默退成文生图 —— 表现就是「怎么画都不像本人」。
    把最像的几个候选捞出来，「对不上」就能一步改好。

    返回 [(文件名, 相似度 0~1)]，按相似度降序，最多 limit 条。
    相似度低于 0.34 的直接丢掉：宁可不说，也别乱猜。
    """
    name = str(persona_name or "").strip()
    if not name:
        return []
    files = list_ref_files(dir_path)
    if not files:
        return []

    def _norm(text: Any) -> str:
        low = str(text or "").strip().lower()
        return LOOSE_STRIP_RE.sub("", low) or low

    target = _norm(name)
    scored: list[tuple[str, float]] = []
    for stem, full in files:
        cand = _norm(stem)
        ratio = (
            difflib.SequenceMatcher(None, target, cand).ratio()
            if (target and cand)
            else 0.0
        )
        # 互相包含是很强的信号（`Aliya形象` / `Aliya立绘` vs `Aliya`），额外加权
        if target and cand and (target in cand or cand in target):
            ratio = max(ratio, 0.8)
        scored.append((os.path.basename(full), ratio))
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return [(n, r) for n, r in scored if r >= 0.34][: max(1, int(limit))]


def suggest_text(dir_path: Any, persona_name: Any, limit: int = 3) -> str:
    """把 suggest_ref 的结果拼成一段能直接照做的建议；没有候选就返回空串。"""
    hits = suggest_ref(dir_path, persona_name, limit)
    if not hits:
        return ""
    name = str(persona_name or "").strip()
    parts = "、".join(f"{n}（{r * 100:.0f}%）" for n, r in hits)
    first = hits[0][0]
    ext = os.path.splitext(first)[1] or ".png"
    safe_name = sanitize_name(name) or "人格名"
    return (
        f"🤔 目录里有名字接近的图：{parts}\n"
        f"   如果 {first} 就是「{name}」的形象，两个办法二选一：\n"
        f"   1) 把文件改名成 `{safe_name}{ext}`；\n"
        f"   2) 或在插件配置项 reference_alias_map 里加一行 `{name}={first}`。"
    )


def describe_refs(dir_path: Any, known_personas: list[str] | None = None) -> str:
    """给 `/画图参考` 用的说明文本。"""
    path = str(dir_path or "").strip()
    files = list_ref_files(dir_path)
    lines = [f"📂 参考图目录：{path or '（未配置）'}"]
    if not os.path.isdir(path):
        lines.append("❌ 这个目录不存在。请手动创建，并把参考图放进去。")
        lines.append("")
    if not files:
        lines.append("（目录里还没有图片）")
    else:
        lines.append(f"共 {len(files)} 张：")
        for stem, full in files:
            try:
                size = os.path.getsize(full)
                lines.append(f"· {os.path.basename(full)}（{size / 1024:.0f} KB）")
            except OSError:
                lines.append(f"· {os.path.basename(full)}")

    if known_personas:
        lines.append("")
        lines.append("当前已有的人格：")
        scan = scan_ref_dir(dir_path)
        for name in known_personas:
            hit, how = resolve_ref(dir_path, name, None, scan)
            mark = (
                f"✅ {os.path.basename(hit)}（{how}）" if hit else "❌ 没有对应参考图"
            )
            lines.append(f"· {name} → {mark}")

    lines.append("")
    lines.append(
        "命名规则：文件名（不含扩展名）= 人格名，例如人格叫「橙橘」就放 `橙橘.png`。\n"
        "名字对不上时，可在配置项 reference_alias_map 里写一行 `人格名=文件名` 做映射。\n"
        "支持格式：" + " / ".join(IMAGE_EXT_ORDER)
    )
    return "\n".join(lines)
