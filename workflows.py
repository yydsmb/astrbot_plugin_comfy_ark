"""本地 ComfyUI 工作流的「可选清单」—— 纯逻辑，零 astrbot 依赖。

为什么要有这一层
----------------
v2.0.x 只有两个固定角色：一个文生图、一个参考图。而手上其实有多套微调模型，
各自适配的场景完全不同 —— 美感版通用且快、精准版还原特定二次元角色但必须英文
提示词、参考版吃参考图。**该用哪套只有模型自己知道**：用户说「画流萤」和
「画只猫」需要的工作流根本不是同一个。所以这里把工作流目录扫成一份
「名字 + 用途」清单交给模型挑，而不是把选择写死在配置里。

目录即清单（不用手填配置）
------------------------
· 目录下每个 ``*.json`` = 一个候选工作流，必须是 ComfyUI「导出（API）」格式；
· 同名 ``.txt`` 提供说明 —— 首行的 ``【名称】`` 是给模型看的名字，余下是用途；
· 没有 .txt 也能用，退回用文件名当名字；
· 以 ``_`` / ``.`` 开头的文件跳过（留给备份和临时禁用）。

这样加一套新工作流只要丢两个文件进目录，不改代码、不改配置。
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass

# 首行的 【名称】 / [名称]
_NAME_RE = re.compile(r"^[【\[]\s*(.+?)\s*[】\]]\s*$")
# 名称里的补充说明：「美感版（快）」与「美感版」应当能互相匹配
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")

# 名称字段的展示上限（超过就截断，避免有人往 .txt 首行写一整段）
_NAME_MAX = 24


@dataclass(frozen=True)
class WorkflowEntry:
    """清单里的一项。只有「怎么称呼它、它在哪、它干什么」三件事。"""

    key: str  # 唯一 id（文件名去掉扩展名），也是日志里的标识
    name: str  # 给人和模型看的名字
    path: str  # API 格式 JSON 的绝对路径
    desc: str = ""  # 用途说明（可能多行）

    @property
    def tagged(self) -> bool:
        """作者有没有认真标注过用途（有 .txt）。用来决定排序与诊断里的提示。"""
        return bool(self.desc.strip())

    def line_for_llm(self, limit: int = 64) -> str:
        """压成一行喂给模型 —— 换行会打断清单的视觉结构，必须合并。"""
        text = " ".join(self.desc.split())
        if len(text) > limit:
            text = text[: limit - 1] + "…"
        return f"· {self.name}：{text}" if text else f"· {self.name}"


def read_caption(json_path: str) -> tuple[str, str]:
    """读同名 ``.txt``，返回 (名字, 说明)。没有或读不了就退回文件名。"""
    stem = os.path.splitext(os.path.basename(json_path))[0]
    txt_path = os.path.splitext(json_path)[0] + ".txt"
    try:
        with open(txt_path, encoding="utf-8-sig") as f:
            raw = f.read()
    except OSError:
        return stem, ""

    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return stem, ""

    head = lines[0]
    m = _NAME_RE.match(head)
    if m:
        name = (m.group(1).strip() or stem)[:_NAME_MAX]
        rest = lines[1:]
    else:
        # 首行不是【名称】格式：短的话就当名字用，长的话退回文件名
        if len(head) <= _NAME_MAX:
            name, rest = head, lines[1:]
        else:
            name, rest = stem, lines
    return name, "\n".join(rest)


def scan_dir(dir_path: str) -> list[WorkflowEntry]:
    """扫目录得到清单。

    排序：标注过用途的在前，其次按名字。**这个顺序就是模型看到的顺序**，
    所以「作者认真写过说明」的项会优先被模型注意到。
    """
    if not dir_path or not os.path.isdir(dir_path):
        return []
    try:
        names = sorted(os.listdir(dir_path))
    except OSError:
        return []

    out: list[WorkflowEntry] = []
    for fname in names:
        if fname.startswith(("_", ".")):
            continue
        if not fname.lower().endswith(".json"):
            continue
        path = os.path.join(dir_path, fname)
        if not os.path.isfile(path):
            continue
        name, desc = read_caption(path)
        out.append(
            WorkflowEntry(
                key=os.path.splitext(fname)[0],
                name=name,
                path=path,
                desc=desc,
            )
        )
    # 排序同时决定两件事：模型看到的先后，以及「没点名时挑第一套」的结果。
    # 按**文件名**而不是显示名 —— 中文名的排序结果不可预测，而文件名是用户
    # 能直接控制顺序的地方（想让某套优先，给它加个 `01_` 前缀即可）。
    out.sort(key=lambda e: (not e.tagged, e.key))
    return out


def describe_for_llm(entries: list[WorkflowEntry], limit: int = 64) -> str:
    """拼给模型看的清单（一行一个）。空清单返回空串。"""
    return "\n".join(e.line_for_llm(limit) for e in entries)


def _norm(text: str) -> str:
    """归一化用于匹配：去括号补充、去分隔符、统一小写。"""
    return (
        _PAREN_RE.sub("", str(text or ""))
        .strip()
        .lower()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )


def match(entries: list[WorkflowEntry], want: str) -> tuple[WorkflowEntry | None, str]:
    """把模型给的工作流名落到具体项上。

    四级匹配：精确 → 去括号精确 → 包含 → 近似。全都不中时返回一句
    「可选有哪些」，让调用方能如实回话，而不是静默换一套工作流跑掉。
    """
    text = str(want or "").strip()
    if not text:
        return None, ""
    if not entries:
        return None, "工作流目录里没有任何可用工作流。"

    for e in entries:
        if text == e.name or text == e.key:
            return e, ""

    n = _norm(text)
    for e in entries:
        if n and n in (_norm(e.name), _norm(e.key)):
            return e, ""

    # 包含匹配：至少两个字符才做，否则「版」这种单字会匹配到一堆
    if len(n) >= 2:
        for e in entries:
            en, ek = _norm(e.name), _norm(e.key)
            if n in en or en in n or n in ek:
                return e, ""

    pool: dict[str, WorkflowEntry] = {}
    for e in entries:
        pool[e.name] = e
        pool.setdefault(e.key, e)
    hit = difflib.get_close_matches(text, list(pool), n=1, cutoff=0.5)
    if hit:
        return pool[hit[0]], ""

    options = "、".join(e.name for e in entries)
    return None, f"没有叫「{text}」的工作流。可选：{options}。"


def find_by_name(entries: list[WorkflowEntry], want: str) -> WorkflowEntry | None:
    """宽松查找（配置项里指定默认工作流时用，找不到就静默回落）。"""
    entry, _why = match(entries, want)
    return entry
