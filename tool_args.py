"""LLM 工具调用的参数容错层（纯逻辑，零 astrbot 依赖）。

为什么需要这一层
----------------
「模型自己发明参数名」是工具调用里最隐蔽的一类失败。它有两种表现：

1. 编了一个从来没存在过的名字（``reference`` / ``use_ref`` / ``ref_source``）——
   框架会把不认识的参数原样传进来，Python 抛 ``TypeError: unexpected keyword
   argument``，被框架兜成一句工具报错。
2. 编了一个**曾经存在过**的名字（``use_reference_image``）—— 连报错都没有，
   功能静默降级。

第 2 种在实测里真的发生了。日志里抓到的是：

    'use_reference_image': False

插件契约里只有 ``reference_source``，模型却写了这么个开关，于是参考图永远不生效、
每次都退成纯文生图，而用户与插件都不发一言。

结论：工具接口的契约是**插件自己的责任**，不能推给每个人格提示词去手工声明
（人格一多、模型一换，这个坑就会反复出现）。本模块做两件事：

* ``normalize_tool_args()`` —— 参数名与取值的归一化。把常见的幻觉写法翻译回
  ``reference_source``；布尔值按语义映射（``True`` → ``auto``，``False`` → ``none``）。
* ``describe_arg_fixes()`` —— 如实报告。未知参数、认不出来的取值都不静默丢弃，
  而是产出可读说明，供调用方记日志、回执、或回灌给模型自我纠正。

不碰网络、不碰文件、不依赖 astrbot，所以可以直接单测。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# ----------------------------------------------------------------------
# 规范参数
# ----------------------------------------------------------------------
CANONICAL_REF_PARAM = "reference_source"
# v3.1.0：让模型自己挑本地工作流。云端后端不区分工作流，这个参数只对本地生效。
CANONICAL_WORKFLOW_PARAM = "workflow"

REF_SOURCE_VALUES = ("auto", "persona", "message", "none")

# 本工具**真实**存在的参数。除了这几个，其它一律算「未知参数」。
REAL_PARAMS = (
    "prompt",
    CANONICAL_REF_PARAM,
    CANONICAL_WORKFLOW_PARAM,
    "size",
    "count",
)

# v2.0.2 起不再对外暴露的旧参数（历史遗留，仍作兼容接收）
RETIRED_PARAM = "use_reference_image"


def _norm_key(name: Any) -> str:
    """键名规范化：小写 + 去掉下划线/连字符/空白。

    模型写 ``ReferenceSource`` / ``reference-source`` / ``reference source``
    都指向同一个参数，归一化之后才比得动。
    """
    return re.sub(r"[\s_\-]+", "", str(name or "")).lower()


# 参考图类参数名的各种写法。**顺序即优先级**（越靠前越优先；
# 规范名排第一，保证它永远赢过任何别名）。
REF_PARAM_ALIASES: tuple[str, ...] = (
    CANONICAL_REF_PARAM,
    "ref_source",
    "reference_source_type",
    "ref_source_type",
    "reference_image",
    "reference_images",
    "reference_type",
    "reference_mode",
    "ref_mode",
    "use_reference_image",
    "use_reference_images",
    "use_reference",
    "use_ref_image",
    "use_ref",
    "use_image_reference",
    "use_img_reference",
    "with_reference_image",
    "with_reference",
    "with_ref",
    "use_persona_reference",
    "use_persona_image",
    "use_personality_reference",
    "persona_reference",
    "persona_image",
    "persona_ref",
    "image_reference",
    "image_ref",
    "img_reference",
    "img_ref",
    "reference",
    "ref",
    "source",
)

_ALIAS_ORDER: tuple[str, ...] = tuple(_norm_key(a) for a in REF_PARAM_ALIASES)
_ALIAS_NORMS = frozenset(_ALIAS_ORDER)
_CANON_NORM = _norm_key(CANONICAL_REF_PARAM)
_ALIAS_RANK: dict[str, int] = {n: i for i, n in enumerate(_ALIAS_ORDER)}

# 工作流类参数名的各种写法，同样「顺序即优先级」。
# 刻意**不**收 "style" / "model" —— 它们更像在描述画风或云端模型，
# 收进来会把模型正常的风格描述误判成工作流名，反而制造新的误会。
WORKFLOW_PARAM_ALIASES: tuple[str, ...] = (
    CANONICAL_WORKFLOW_PARAM,
    "workflow_name",
    "workflow_id",
    "workflow_type",
    "workflow_preset",
    "workflow_mode",
    "wokflow",
    "pipeline",
    "preset",
    "preset_name",
    "工作流",
    "工作流名",
    "工作流名称",
    "预设",
)

_WF_ORDER: tuple[str, ...] = tuple(_norm_key(a) for a in WORKFLOW_PARAM_ALIASES)
_WF_NORMS = frozenset(_WF_ORDER)
_CANON_WF_NORM = _norm_key(CANONICAL_WORKFLOW_PARAM)
# 用 setdefault 而不是字典推导：别名归一化后可能互相撞车（"work_flow" 与
# "workflow" 去掉下划线就是同一个词），推导式会让**靠后的**索引覆盖靠前的，
# 于是规范名的优先级被自己的别名压下去（实测踩过：同时写两个参数时选错了那个）。
_WF_RANK: dict[str, int] = {}
for _i, _name in enumerate(_WF_ORDER):
    _WF_RANK.setdefault(_name, _i)


# ----------------------------------------------------------------------
# 取值归一化
# ----------------------------------------------------------------------
_RAW_VALUE_MAP: dict[str, str] = {
    # ---- auto：交回插件按配置自己挑 ----
    "auto": "auto",
    "default": "auto",
    "any": "auto",
    "unspecified": "auto",
    "none_specified": "auto",
    "自动": "auto",
    "默认": "auto",
    "自动判断": "auto",
    "自动选择": "auto",
    "未知": "auto",
    "": "auto",
    # ---- persona：当前 AI 人格图 ----
    "persona": "persona",
    "personality": "persona",
    "persona_image": "persona",
    "personaimage": "persona",
    "use_persona": "persona",
    "self": "persona",
    "selfie": "persona",
    "self_portrait": "persona",
    "selfportrait": "persona",
    "selfie_photo": "persona",
    "character": "persona",
    "character_image": "persona",
    "ai": "persona",
    "you": "persona",
    "yourself": "persona",
    "yourself_image": "persona",
    "me": "persona",
    "my": "persona",
    "avatar": "persona",
    "portrait": "persona",
    "doll": "persona",
    "人格": "persona",
    "人格图": "persona",
    "人格参考图": "persona",
    "角色": "persona",
    "角色图": "persona",
    "自己": "persona",
    "本人": "persona",
    "我": "persona",
    "立绘": "persona",
    "头像": "persona",
    "自拍": "persona",
    # ---- message：本次消息带的图 ----
    "message": "message",
    "message_image": "message",
    "msg": "message",
    "user": "message",
    "user_image": "message",
    "attached": "message",
    "attachment": "message",
    "upload": "message",
    "uploaded": "message",
    "input": "message",
    "input_image": "message",
    "image": "message",
    "images": "message",
    "photo": "message",
    "picture": "message",
    "引用": "message",
    "引用图": "message",
    "消息图": "message",
    "附图": "message",
    "图片": "message",
    "用户图": "message",
    "上传图": "message",
    # ---- none：不用参考图 ----
    "none": "none",
    "no": "none",
    "off": "none",
    "false": "none",
    "never": "none",
    "disable": "none",
    "disabled": "none",
    "without": "none",
    "no_reference": "none",
    "noreference": "none",
    "no_ref": "none",
    "noref": "none",
    "without_reference": "none",
    "text": "none",
    "text_only": "none",
    "txt2img": "none",
    "t2i": "none",
    "无": "none",
    "不用": "none",
    "不要": "none",
    "不": "none",
    "关闭": "none",
    "禁用": "none",
    "纯文本": "none",
    "文生图": "none",
}

# 键也做同样的规范化（去掉下划线等），这样 "no_reference" 与 "noreference" 等价
VALUE_MAP: dict[str, str] = {_norm_key(k): v for k, v in _RAW_VALUE_MAP.items()}

# 关键词回退表：精确匹配失败后按顺序扫子串。
# 顺序重要 —— "人格图" 里含 "图"，必须先判 persona 再判 message/none。
_VALUE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("persona", "persona"),
    ("personality", "persona"),
    ("selfie", "persona"),
    ("self", "persona"),
    ("人格", "persona"),
    ("角色", "persona"),
    ("自己", "persona"),
    ("avatar", "persona"),
    ("noreference", "none"),
    ("noref", "none"),
    ("without", "none"),
    ("disable", "none"),
    ("text", "none"),
    ("纯文本", "none"),
    ("文生图", "none"),
    ("不用", "none"),
    ("不要", "none"),
    ("关闭", "none"),
    ("message", "message"),
    ("msg", "message"),
    ("attach", "message"),
    ("upload", "message"),
    ("photo", "message"),
    ("picture", "message"),
    ("附图", "message"),
    ("消息图", "message"),
    ("引用", "message"),
    ("auto", "auto"),
    ("default", "auto"),
    ("自动", "auto"),
    ("默认", "auto"),
)


def normalize_ref_value(value: Any) -> tuple[str, bool]:
    """把任意取值翻译成 ref_source。

    返回 ``(归一化后的值, 是否认得这个值)``。

    * 布尔 / 数字按真值映射：``True → auto``，``False → none``。
      ``True`` 不直接钉成 ``persona`` 是有意的 —— 布尔只表达「允许用参考图」，
      而 ``auto`` 会按配置的 ``reference_priority`` 去挑，尊重用户的偏好；
      默认配置（persona 优先）下 ``auto`` 的最终结果就是人格图。
    * 字符串先精确查表，再按关键词回退（``"use_persona_image"`` → persona）。
    * 认不出来时返回 ``("auto", False)``，由调用方负责把这件事说出来。
    """
    if value is None:
        return "auto", True
    if isinstance(value, bool):
        return ("auto" if value else "none"), True
    if isinstance(value, (int, float)):
        return ("auto" if value else "none"), True

    s = str(value).strip().strip("\"'").strip()
    if not s:
        return "auto", True

    key = _norm_key(s)
    if key in VALUE_MAP:
        return VALUE_MAP[key], True

    low = s.lower()
    for token, result in _VALUE_KEYWORDS:
        if token in low:
            return result, True

    return "auto", False


def _is_canonical_literal(value: Any) -> bool:
    """取值是否本来就是四个规范值之一（用来判断有没有做过翻译）。"""
    return isinstance(value, str) and value.strip().lower() in REF_SOURCE_VALUES


def _short(value: Any, limit: int = 40) -> str:
    s = repr(value) if not isinstance(value, str) else value
    s = s.replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ----------------------------------------------------------------------
# 归一化结果
# ----------------------------------------------------------------------
@dataclass
class NormalizedArgs:
    """一次工具调用里「参考图参数」的归一化结果 + 全部异常情况。"""

    ref_source: str = "auto"
    """最终采用的参考图来源（一定是 REF_SOURCE_VALUES 之一）。"""

    used_param: str = ""
    """实际被采纳的参数名（空 = 模型压根没给参考图类参数）。保留模型的原始写法。"""

    used_value: Any = None
    """实际被采纳的原始取值（保留原样，便于回显）。"""

    from_canonical: bool = False
    """是否本来就是规范参数名 ``reference_source``。"""

    bad_value: str = ""
    """取值认不出来时的原文（空 = 没问题）。"""

    ref_params: list[str] = field(default_factory=list)
    """本次调用里出现的**全部**参考图类参数名（含被忽略的）。"""

    ignored_ref_params: list[str] = field(default_factory=list)
    """因为规范名已存在而被忽略的别名。"""

    unknown_params: list[str] = field(default_factory=list)
    """本工具根本没有的参数名（模型凭空发明的）。"""

    workflow: str = ""
    """最终采用的工作流名；空 = 模型没指定，由插件按需求自己挑。"""

    workflow_param: str = ""
    """实际被采纳的工作流参数名（保留模型的原始写法）。"""

    workflow_raw: Any = None
    """工作流参数的原始取值（取值不做映射，交给工作流清单去匹配）。"""

    ignored_workflow_params: list[str] = field(default_factory=list)
    """因为规范名已存在而被忽略的工作流别名。"""

    @property
    def coerced(self) -> bool:
        """是否发生过「参数名或取值」的翻译。"""
        if not self.used_param:
            return False
        if not self.from_canonical:
            return True
        return not _is_canonical_literal(self.used_value)

    @property
    def workflow_coerced(self) -> bool:
        """工作流参数名被翻译过吗。取值不做映射 —— 那是工作流清单的活儿。"""
        if not self.workflow_param:
            return False
        return _norm_key(self.workflow_param) != _CANON_WF_NORM

    @property
    def has_news(self) -> bool:
        """有没有需要说出来的事。"""
        return bool(
            self.coerced
            or self.bad_value
            or self.ignored_ref_params
            or self.unknown_params
            or self.workflow_coerced
            or self.ignored_workflow_params
        )


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def normalize_tool_args(
    args: Mapping[str, Any] | None,
    *,
    known: Iterable[str] = REAL_PARAMS,
) -> NormalizedArgs:
    """把一次工具调用的全部参数过一遍容错层。

    ``args`` 应当是**模型发来的原始参数**（含 ``reference_source``，
    如果模型确实写了它）。调用方若无法区分「模型没写」与「写了默认值」，
    就别把默认值塞进来 —— 用一个哨兵值判断后再合并。
    """
    norm = NormalizedArgs()
    if not args:
        return norm

    known_set = set(known)

    # 1) 出现的参考图类参数，按别名优先级排序
    present: list[str] = []
    for key in args:
        if _norm_key(key) in _ALIAS_NORMS and key not in present:
            present.append(key)
    present.sort(key=lambda k: _ALIAS_RANK.get(_norm_key(k), len(_ALIAS_ORDER)))
    norm.ref_params = list(present)

    # 2) 挑一个用：规范名永远优先，其次按别名表顺序
    if present:
        pick = present[0]
        norm.used_param = pick
        norm.used_value = args[pick]
        norm.from_canonical = _norm_key(pick) == _CANON_NORM
        value, ok = normalize_ref_value(norm.used_value)
        norm.ref_source = value if value in REF_SOURCE_VALUES else "auto"
        if not ok:
            norm.bad_value = _short(norm.used_value)
        norm.ignored_ref_params = [k for k in present if k != pick]

    # 2.5) 工作流参数：同样按别名优先级挑一个。
    # 取值不在这里归一 —— 「美感版」这种名字属于工作流清单，只有插件知道有哪些。
    wf_present: list[str] = []
    for key in args:
        if _norm_key(key) in _WF_NORMS and key not in wf_present:
            wf_present.append(key)
    wf_present.sort(key=lambda k: _WF_RANK.get(_norm_key(k), len(_WF_ORDER)))
    if wf_present:
        pick_wf = wf_present[0]
        norm.workflow_param = pick_wf
        norm.workflow_raw = args[pick_wf]
        norm.workflow = coerce_str(norm.workflow_raw).strip()
        norm.ignored_workflow_params = [k for k in wf_present if k != pick_wf]

    # 3) 未知参数：既不在真实参数表里，也不是参考图 / 工作流类的别名
    norm.unknown_params = [
        str(k)
        for k in args
        if k not in known_set
        and _norm_key(k) not in _ALIAS_NORMS
        and _norm_key(k) not in _WF_NORMS
    ]

    return norm


def describe_arg_fixes(norm: NormalizedArgs) -> tuple[str, str]:
    """把归一化结果翻成人话。→ ``(给 LLM 的说明, 给用户的说明)``

    两边都为空字符串表示「什么都没发生」，调用方无需附加任何东西。
    """
    if not norm.has_news:
        return "", ""

    llm_bits: list[str] = []
    user_bits: list[str] = []

    if norm.coerced:
        shown = f"{norm.used_param}={_short(norm.used_value)}"
        llm_bits.append(
            f"你传的是 {shown}，插件已理解为 reference_source={norm.ref_source}"
        )
        user_bits.append(
            f"已自动纠正参数 {norm.used_param} → reference_source={norm.ref_source}"
        )
        if _norm_key(norm.used_param) == _norm_key(RETIRED_PARAM):
            llm_bits.append(f"{RETIRED_PARAM} 这个开关已经取消（布尔值只作兼容）")
    if norm.bad_value:
        llm_bits.append(f"取值 {norm.bad_value} 认不出来，已按 {norm.ref_source} 处理")
        user_bits.append(
            f"参数取值 {norm.bad_value} 无法识别，已按 {norm.ref_source} 处理"
        )
    if norm.ignored_ref_params:
        # 模型同时写了规范名和别名 —— 规范名赢了，但也要说清楚，免得它以为生效了
        llm_bits.append(
            "同时出现的 "
            + "、".join(norm.ignored_ref_params)
            + " 已被忽略（你写了两个开关）"
        )
    if norm.workflow_coerced:
        llm_bits.append(
            f"你传的是 {norm.workflow_param}={_short(norm.workflow_raw)}，"
            f"插件已理解为 workflow={_short(norm.workflow)}"
        )
    if norm.ignored_workflow_params:
        llm_bits.append(
            "同时出现的 "
            + "、".join(norm.ignored_workflow_params)
            + " 已被忽略（工作流参数只认一个）"
        )
    if norm.unknown_params:
        names = "、".join(norm.unknown_params)
        llm_bits.append(f"参数 {names} 本工具没有，已忽略")
        user_bits.append(f"已忽略无法识别的参数：{names}")

    params_zh = "/".join(REAL_PARAMS)
    llm_tip = (
        "\n（工具参数提示："
        + "；".join(llm_bits)
        + f"。本工具的参数只有 {params_zh}，参考图一律用 {CANONICAL_REF_PARAM}，"
        f"工作流一律用 {CANONICAL_WORKFLOW_PARAM}，不要自己另起名字。）"
    )
    user_tip = "（" + "；".join(user_bits) + "）" if user_bits else ""
    return llm_tip, user_tip


# ----------------------------------------------------------------------
# 标量参数的类型容错
# ----------------------------------------------------------------------
def coerce_str(value: Any) -> str:
    """模型偶尔把字符串参数写成数字，别让 ``.strip()`` 直接炸掉。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def coerce_int(value: Any) -> int | None:
    """把 ``"2"`` / ``2.0`` / ``"共 2 张"`` 都读成 int；读不出返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        try:
            return int(s)
        except ValueError:
            m = re.search(r"-?\d+", s)
            if m:
                try:
                    return int(m.group())
                except ValueError:
                    return None
    return None


_SIZE_SCALE = {"1": "1K", "2": "2K", "3": "3K", "4": "4K"}


def coerce_size(value: Any) -> str | None:
    """``2`` / ``"2k"`` / ``"2 K"`` 都归一化成 ``2K``；其它原样返回。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int) or (
        isinstance(value, float) and float(value).is_integer()
    ):
        return _SIZE_SCALE.get(str(int(value)), f"{int(value)}K")
    if isinstance(value, str):
        s = value.strip()
        m = re.fullmatch(r"([1-4])\s*[kK]", s)
        if m:
            return m.group(1) + "K"
        return s or None
    return None
