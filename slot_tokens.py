"""槽位令牌（slot token）适配层：一套提示词模板，两个后端。

背景
----
Qwen-Image-Edit（本地 ComfyUI 工作流）原生支持 ``<image1>`` / ``<image2>``
这类槽位语法 —— 它指的是「本次输入的第 N 张图」。实测 ``<image1>`` 与
「参考图中的女孩」两种写法在本地工作流里都能生效，所以模板统一用官方槽位
语法，而不是自己发明一种说法。

Seedream（云端火山方舟）没有这套语法，只认自然语言。

于是让 LLM 分别写两版模板是纯浪费 —— 而且写两版必然出现「换后端忘了改
提示词」的翻车。正确做法是：**LLM 永远只写 ``<imageN>``，插件在发送前按
目标后端翻译一次**。

顺带兜住一个真实故障：模型写了 ``<image2>`` 但这次只有一张参考图（或者
一张都没有）。这种「悬空引用」丢给后端，轻则那句话失效，重则让模型照着
一张不存在的图去画。这里按**句子粒度**把引用悬空槽位的整句删掉，并如实
回报删了什么 —— 不静默，也不让整次请求失败。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 目标后端标识（插件内部用，不是后端自己的名字）
BACKEND_QWEN = "qwen"
BACKEND_SEEDREAM = "seedream"

# <image1> / < image 1 > / <IMAGE2> 全认，统一收成 <imageN>
SLOT_RE = re.compile(r"<\s*image\s*(\d+)\s*>", re.IGNORECASE)

# 按句末标点切句，标点**留在前一句末尾**（lookbehind，不消耗）
SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;\n])")

# 云端自然语言说法
_CLOUD_WORDS = {
    1: "上传的参考图",
    2: "第二张参考图",
    3: "第三张参考图",
    4: "第四张参考图",
}

# 整段都被删空时的兜底词：至少让句子读得通，不至于让模型对着一句残话画图
_FALLBACK_WORD = "画面中的主体"


def cloud_word(index: int) -> str:
    """云端后端对「第 N 张输入图」的自然语言说法。"""
    if index in _CLOUD_WORDS:
        return _CLOUD_WORDS[index]
    return f"第{index}张参考图"


def find_slots(text: str) -> list[int]:
    """文本里出现过的槽位序号（升序去重）。"""
    if not text:
        return []
    return sorted({int(m.group(1)) for m in SLOT_RE.finditer(text)})


def has_slots(text: str) -> bool:
    return bool(find_slots(text))


def normalize_slots(text: str) -> str:
    """把 ``< image 1 >`` 这类松散写法收成 ``<image1>``。"""
    if not text:
        return text or ""
    return SLOT_RE.sub(lambda m: f"<image{int(m.group(1))}>", text)


@dataclass
class SlotAdapt:
    """一次适配的结果。`text` 才是能直接发给后端的文本。"""

    text: str = ""
    backend: str = ""
    ref_count: int = 0
    kept: list[int] = field(default_factory=list)
    """序号在参考图张数之内的槽位（正常引用）。"""
    translated: list[int] = field(default_factory=list)
    """被翻译成自然语言的槽位（仅云端）。"""
    dropped: list[int] = field(default_factory=list)
    """悬空槽位序号（引用了本次不存在的参考图）。"""
    dropped_sentences: list[str] = field(default_factory=list)
    """因为悬空槽位被整句删掉的内容。"""
    fell_back: bool = False
    """悬空句删完导致整段为空 —— 已退化成「换中性说法」而非删空。"""

    @property
    def changed(self) -> bool:
        return bool(self.translated or self.dropped or self.fell_back)

    def describe(self) -> str:
        """一行日志用。"""
        return (
            f"槽位适配：后端={self.backend or '?'}｜参考图 {self.ref_count} 张"
            f"｜保留 {self.kept or '[]'}"
            f"｜翻译 {self.translated or '[]'}"
            f"｜丢弃 {self.dropped or '[]'}"
            + ("｜退化为中性说法" if self.fell_back else "")
        )

    def note_zh(self) -> str:
        """给用户回执 / LLM 提示用的一句人话。没有异常时返回空串。"""
        if not self.dropped:
            return ""
        joined = "、".join(f"<image{n}>" for n in self.dropped)
        parts = [
            f"⚠️ 提示词里有 {len(self.dropped)} 处引用了本次不存在的参考图（{joined}），"
        ]
        if self.fell_back:
            parts.append("已改成中性说法。")
        else:
            parts.append(f"已移除 {len(self.dropped_sentences)} 句相关描述。")
        return "".join(parts)

    def note_llm(self) -> str:
        """给 LLM 的纠正提示：下次别引用不存在的图。"""
        if not self.dropped:
            return ""
        used = "、".join(f"<image{n}>" for n in self.dropped)
        if self.ref_count <= 0:
            return (
                f"\n（工具提示：本次**没有**任何参考图，但提示词引用了 {used}。"
                "插件已按实际张数处理。本次属于纯文生图 —— 不要写「与 <image1> 一致」"
                "这类锁身份的句子，改成直接描述主体长相。）"
            )
        return (
            f"\n（工具提示：本次只提供了 {self.ref_count} 张参考图，但提示词里引用了 "
            f"{used}。插件已按实际张数处理。下次写 <imageN> 之前先确认第 N 张图确实存在，"
            "或改用 <image1>。）"
        )


_CJK = "\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
# 「中文 + 空格 + 中文」：槽位换成中文词之后会剩下这类空格，读起来像断句。
# 只收掉两侧都是中日韩字符（含全角标点）之间的空格，英文串里的照旧保留。
_CJK_SPACE_RE = re.compile(r"(?<=[" + _CJK + r"])[ \t]+(?=[" + _CJK + r"])")


def tidy_cjk_spaces(text: str) -> str:
    """收掉「中文 空格 中文」里多余的空格（槽位翻译后的副产物）。"""
    if not text:
        return text or ""
    return _CJK_SPACE_RE.sub("", text)


def _translate(text: str, backend: str, res: SlotAdapt) -> str:
    if backend != BACKEND_SEEDREAM:
        # Qwen（以及任何原生支持槽位的后端）：原样保留，只做写法规范化
        return SLOT_RE.sub(lambda m: f"<image{int(m.group(1))}>", text)

    seen = set()

    def repl(m):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            res.translated.append(n)
        return cloud_word(n)

    return tidy_cjk_spaces(SLOT_RE.sub(repl, text))


def adapt_slots(text: str, *, backend: str, ref_count: int) -> SlotAdapt:
    """把提示词适配到某个后端。

    Args:
        text: LLM 写出来的提示词（可能含 ``<image1>`` 等槽位）。
        backend: ``BACKEND_QWEN`` 保留槽位；``BACKEND_SEEDREAM`` 翻译成自然语言。
        ref_count: 本次**实际**提供了几张参考图。序号超过它的槽位算悬空引用。

    Returns:
        SlotAdapt，用 ``.text`` 取适配后的文本。
    """
    norm = normalize_slots(text or "")
    n_ref = max(0, int(ref_count or 0))
    res = SlotAdapt(text=norm, backend=backend, ref_count=n_ref)

    slots = find_slots(norm)
    if not slots:
        return res

    res.kept = [n for n in slots if n <= n_ref]
    res.dropped = [n for n in slots if n > n_ref]

    if not res.dropped:
        res.text = _translate(norm, backend, res)
        return res

    # 有悬空引用：按句删。只删「引用了不存在那张图」的句子。
    kept_sentences: list[str] = []
    for sentence in SENT_SPLIT_RE.split(norm):
        if not sentence:
            continue
        if any(n > n_ref for n in find_slots(sentence)):
            res.dropped_sentences.append(sentence.strip())
            continue
        kept_sentences.append(sentence)

    merged = "".join(kept_sentences).strip()
    if merged:
        res.text = _translate(merged, backend, res)
        return res

    # 全删空了（整段就是一句锁身份的引用）—— 退化成中性说法，别交一句空提示词
    res.fell_back = True
    res.dropped_sentences = []
    res.text = SLOT_RE.sub(_FALLBACK_WORD, norm).strip()
    return res
