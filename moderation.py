"""内容审查（审查提示词 + 审查图片）的纯逻辑层 —— 零 astrbot 依赖。

为什么需要它：
  云端（火山方舟）自己带审核，生成前的提示词与生成后的图都会被上游拦。
  **本地 ComfyUI 完全没有审核** —— 而本地跑的恰恰是能出 R18 的开源模型。
  所以「本地优先」必须配一道自己的审查闸门，两道：
    ① 生成前查提示词（纯文本调用，很便宜）
    ② 生成后查图片（把图交给用户指定的多模态模型看）

设计取舍：
  · 审查模型必须由用户显式指定（多模态能力因模型而异，猜不得）。
  · 解析要够皮实：模型不一定老老实实按格式答，所以先按规定格式解析，
    再退到关键词扫描，最后才认「解析不出来」。
  · 「解析不出来」和「判为安全」是两件事：前者 ok=False，交给上层的
    moderation_fail_open 决定放行还是拦截，绝不默认当成安全。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# ----------------------------------------------------------------------
# 判定词
# ----------------------------------------------------------------------
FLAG_UNSAFE = "UNSAFE"
FLAG_SAFE = "SAFE"

# 明确表达「不安全」的词（小写后比较）
UNSAFE_MARKERS = (
    "unsafe",
    "nsfw",
    "r18",
    "r-18",
    "porn",
    "explicit",
    "不安全",
    "违规",
    "不合规",
    "违禁",
    "涉黄",
    "色情",
    "裸露",
)
# 明确表达「安全」的词
SAFE_MARKERS = ("safe", "安全", "合规", "正常")

# 否定前缀（「不是 R18」这类不能当成命中）
NEGATION_WORDS = "不|非|无|没|未|否"
# 否定词后面常见的连接字：「没有裸露」「不包含色情」——「有/包含」这些不算否定词的一部分
NEGATION_BRIDGE = r"(?:有|是|会|能|包含|涉及|存在|属于|算|显得|看到|出现|含|带)?"
NEGATION_RE = re.compile(rf"(?:{NEGATION_WORDS}){NEGATION_BRIDGE}\s*", re.IGNORECASE)

VERDICT_LINE_RE = re.compile(r"判定\s*[:：]\s*([^\r\n，,。;；]*)")
CATEGORY_LINE_RE = re.compile(r"类别\s*[:：]\s*([^\r\n]*)")
REASON_LINE_RE = re.compile(r"理由\s*[:：]\s*([^\r\n]*)")
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


# ----------------------------------------------------------------------
# 提示词模板
# ----------------------------------------------------------------------
R18_DEFINITION = (
    "R18 / NSFW 的判定范围：\n"
    "  1. 色情或裸露：性器官、性行为、明显的性暗示姿势或服装、露骨的情色描写；\n"
    "  2. 性化未成年人：任何把未成年人（或看起来未成年）性化的内容；\n"
    "  3. 极端血腥暴力：肢解、开膛、内脏外露、虐待；\n"
    "  4. 违禁内容：毒品制作、枪械爆炸物制作、恐怖主义宣传；\n"
    "  5. 其他违法内容。\n"
    "不构成 R18 的常规情况：泳装、运动服、露肩/露背的日常着装、"
    "轻微伤痕、卡通夸张的打击场面、艺术人体但无性意味。"
)

OUTPUT_FORMAT = (
    "只输出下面三行，不要任何其它文字、不要 Markdown、不要解释：\n"
    "判定：SAFE 或 UNSAFE\n"
    "类别：命中的类别名（没有就写「无」）\n"
    "理由：一句话"
)

PROMPT_CHECK_SYSTEM = (
    "你是一个图片生成请求的审核员。你会收到一段「图片提示词」，"
    "任务是在真正生成之前，判断这段提示词会不会导向 R18 / NSFW 或违法内容。\n"
    f"{R18_DEFINITION}\n"
    "注意：提示词是文字描述，要按它**最可能生成的画面**来判断，"
    "而不是只看字面有没有敏感词（例如「手术室的无影灯」不该被判违规）。\n"
    + OUTPUT_FORMAT
)

IMAGE_CHECK_SYSTEM = (
    "你是一个图片内容审核员。你会看到一张刚生成的图片，"
    "任务是判断它是否属于 R18 / NSFW 或违法内容。\n"
    f"{R18_DEFINITION}\n"
    "只根据图片实际呈现的内容判断，不要脑补图片里没有的东西。\n" + OUTPUT_FORMAT
)

UNCERTAIN_STRICT = "如果你拿不准，按 UNSAFE 处理。"
UNCERTAIN_LENIENT = "如果你拿不准，按 SAFE 处理，并在理由里说明你不确定。"


def uncertain_clause(strict: bool) -> str:
    return UNCERTAIN_STRICT if strict else UNCERTAIN_LENIENT


def build_prompt_check_system(strict: bool = False) -> str:
    return PROMPT_CHECK_SYSTEM + "\n" + uncertain_clause(strict)


def build_image_check_system(strict: bool = False) -> str:
    return IMAGE_CHECK_SYSTEM + "\n" + uncertain_clause(strict)


def build_prompt_check_user(prompt: str, *, max_chars: int = 2000) -> str:
    text = str(prompt or "").strip()
    tail = ""
    if len(text) > max_chars:
        # 明说截到多少字 —— 否则模型判 SAFE 时无法分辨「真的安全」还是「关键部分被截掉了」
        tail = (
            f"\n（提示词过长，已只截取前 {max_chars} 字送审，完整长度 {len(text)} 字）"
        )
        text = text[:max_chars] + "…"
    return f"待审核的图片提示词：\n{text}{tail}"


def build_image_check_user(caption: str = "") -> str:
    text = str(caption or "").strip()
    if not text:
        return "请审核这张图片。"
    return f"这张图是用下面这段提示词生成的，可作为参考（但以图片实际内容为准）：\n{text[:600]}"


# ----------------------------------------------------------------------
# 判定结果
# ----------------------------------------------------------------------
@dataclass
class Verdict:
    """一次审查的结果。

    ok       —— 审查**是否成功执行**（模型有没有给出可解析的判定）。
                 ok=False 表示「审不了」，不等于「安全」。
    flagged  —— 是否为 R18 / 违规。
    category —— 命中的类别，没命中为空。
    reason   —— 理由。
    raw      —— 模型原始应答（截断后），用于排障。
    stage    —— "prompt" / "image"，便于回执里说清是哪一道闸门拦的。
    """

    ok: bool = False
    flagged: bool = False
    category: str = ""
    reason: str = ""
    raw: str = ""
    stage: str = ""

    @property
    def label(self) -> str:
        if not self.ok:
            return "无法判定"
        return "命中 R18" if self.flagged else "安全"


def _strip_negated(text: str) -> str:
    """把「不是 R18」「没有裸露」「不包含色情」这类否定说法整个删掉，避免误命中。

    只删「否定词 + 可选连接字 + 标记词」这一段，不碰句子其它部分。
    """
    out = text
    for word in UNSAFE_MARKERS + SAFE_MARKERS:
        pattern = re.compile(
            rf"(?:{NEGATION_WORDS}){NEGATION_BRIDGE}\s*{re.escape(word)}",
            re.IGNORECASE,
        )
        out = pattern.sub(" ", out)
    return out


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


def parse_verdict(text: Any, stage: str = "") -> Verdict:
    """解析审查模型的应答。越靠前的判据越权威。"""
    raw = str(text or "").strip()
    verdict = Verdict(stage=stage, raw=raw[:400])
    if not raw:
        return verdict
    # JSON 应答里额外的字段（类别/理由）先攒着，等三行格式解析完再补
    json_extra: dict[str, str] = {}

    # 1) 严格按约定格式：判定：XXX
    for match in VERDICT_LINE_RE.finditer(raw):
        token = _norm(match.group(1))
        if not token:
            continue
        if FLAG_UNSAFE in token or "unsafe" in token:
            verdict.ok, verdict.flagged = True, True
            break
        if FLAG_SAFE in token or token.startswith("safe"):
            verdict.ok, verdict.flagged = True, False
            break

    # 2) 模型返回了 JSON
    if not verdict.ok:
        block = JSON_BLOCK_RE.search(raw)
        if block:
            try:
                data = json.loads(block.group(0))
            except (json.JSONDecodeError, TypeError):
                data = None
            if isinstance(data, dict):
                merged = " ".join(
                    _norm(data.get(k))
                    for k in (
                        "verdict",
                        "result",
                        "judgement",
                        "judgment",
                        "label",
                        "判定",
                        "标签",
                    )
                ).strip()
                for key in ("category", "categories", "类别", "type"):
                    val = str(data.get(key) or "").strip()
                    if val and val.lower() not in ("none", "n/a", "无", "-"):
                        json_extra["category"] = val
                        break
                for key in ("reason", "reasons", "理由", "explain", "description"):
                    val = str(data.get(key) or "").strip()
                    if val:
                        json_extra["reason"] = val
                        break
                if merged:
                    if "unsafe" in merged or "r18" in merged or "nsfw" in merged:
                        verdict.ok, verdict.flagged = True, True
                    elif "safe" in merged:
                        verdict.ok, verdict.flagged = True, False

    # 3) 关键词扫描（先剔掉否定说法）
    if not verdict.ok:
        cleaned = _strip_negated(raw).lower()
        has_unsafe = any(m in cleaned for m in UNSAFE_MARKERS)
        has_safe = any(m in cleaned for m in SAFE_MARKERS)
        if has_unsafe and not has_safe:
            verdict.ok, verdict.flagged = True, True
        elif has_safe and not has_unsafe:
            verdict.ok, verdict.flagged = True, False
        elif has_unsafe and has_safe:
            # 两者都出现：约定格式已经查过了，这里保守取「不安全」
            verdict.ok, verdict.flagged = True, True

    # 类别与理由（解析不到不影响判定）
    m = CATEGORY_LINE_RE.search(raw)
    if m:
        cat = m.group(1).strip()
        if cat and cat not in ("无", "None", "none", "-", "N/A"):
            verdict.category = cat[:40]
    if not verdict.category:
        verdict.category = json_extra.get("category", "")[:40]
    m = REASON_LINE_RE.search(raw)
    if m:
        verdict.reason = m.group(1).strip()[:300]
    if not verdict.reason:
        verdict.reason = json_extra.get("reason", "")[:300]
    if not verdict.reason:
        # 没有「理由：」时退而取第一行既不是「判定/类别」、也不只是判定词的内容
        bare_tokens = set(UNSAFE_MARKERS) | set(SAFE_MARKERS)
        for line in raw.splitlines():
            line = line.strip()
            if not line or VERDICT_LINE_RE.match(line) or CATEGORY_LINE_RE.match(line):
                continue
            if line.lower().strip("。.！! ") in bare_tokens:
                continue
            verdict.reason = line[:300]
            break
    return verdict


# ----------------------------------------------------------------------
# 回执文案
# ----------------------------------------------------------------------
STAGE_ZH = {"prompt": "提示词", "image": "生成的图片"}


def verdict_receipt(verdict: Verdict, *, action: str = "block") -> str:
    """把判定变成给用户看的一句话。"""
    stage = STAGE_ZH.get(verdict.stage, verdict.stage or "内容")

    if not verdict.ok:
        return f"⚠️ 审查未完成（{stage}审查模型没有给出可识别的判定）。"

    if not verdict.flagged:
        return ""

    bits = [f"🚫 {stage}未通过审查（判为 R18 / 违规）"]
    if verdict.category:
        bits.append(f"类别：{verdict.category}")
    if verdict.reason:
        bits.append(f"理由：{verdict.reason}")
    if action == "block":
        bits.append("图片已拦截，不会发送。")
    bits.append(
        "如果你确信这是误判，把插件配置里的 moderation_strict 关掉"
        "（拿不准时按安全处理），或把 moderation_enabled 关掉。"
    )
    return "\n".join(bits)


def unclear_receipt(verdict: Verdict, *, fail_open: bool) -> str:
    stage = STAGE_ZH.get(verdict.stage, verdict.stage or "内容")
    if fail_open:
        return f"⚠️ {stage}审查未得出结论（已按配置放行）。原始应答：{verdict.raw[:120]}"
    return (
        f"⚠️ {stage}审查未得出结论，按配置（moderation_fail_open=false）拦截。"
        f"原始应答：{verdict.raw[:120]}"
    )
