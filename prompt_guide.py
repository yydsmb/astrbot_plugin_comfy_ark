"""注入 system_prompt 的那段「生图前自查」。

为什么单独一个模块
------------------
完整指令（含写 prompt 的固定模板、变量规则、few-shot 示例）有一千多字，
**全塞进 system_prompt 会把人格提示词挤掉注意力**。所以拆两处：

* ``system_prompt`` —— 只放「决策规则 + 动态事实」，短、硬、必须执行；
* 工具说明（``comfy_ark_generate_image`` 的 docstring）—— 放完整模板与示例，
  模型要调用工具时必然看到。

本模块只管前者。文本里含 ``<image1>`` 字面量是**故意**的：那是给模型看的
书写约定，不是要发给后端的文本 —— 发送前由 ``slot_tokens`` 按后端翻译。
"""

from __future__ import annotations

# 幂等标记：注入前先看 system_prompt 里有没有它。
# 不能用太通用的词（「参考图」之类），否则会误判成人格提示词里已有的内容。
SYSTEM_HEADER = "【生图前自查 · 调用 comfy_ark_generate_image 前必须执行】"

# 决策规则正文（不含动态事实行）。
SYSTEM_RULES = """一、判断 reference_source
· 画面主体是「你 / AI / 当前人格」（自己、自拍、全身、半身、局部特写、拍腿、
  拍手、拍任何部位、立绘、换装、再拍一张、换个姿势、你现在的样子）→ "persona"
· 用户带了图，要求照着改 → "message"
· 画面里没有你、也没给图 → "none"
· 同时出现「画你」和「用户图」→ 主体优先，选 "persona"，用户图作为换装/背景来源
· 拿不准 → 不传参数，插件 auto
铁律：只要画面里是你自己 —— 哪怕只是拍腿、拍手这种没有脸的局部 —— 也必须传
"persona"，**严禁**传 "none"。参考图负责提供服装与身体比例，模型自己知道
该取哪一部分。只有画一个完全无关的人或物体时，才用 "none"。

二、写 prompt
有参考图时，只描述「要变的东西」，不描述「要保留的身份」。
铁律：脸型和发型必须与 <image1> 完全一致，任何情况下都不能改。
禁止出现：脸型、发色、瞳色、眼睛、五官、外貌、可爱、漂亮、精致。
可替换变量：新场景、新动作、构图（全身/半身/自拍/特写/头像）、新服装。
服装可以换：AI 可根据场景、情绪、剧情自主判断（如洗澡、睡觉、出舱、节日），不必等用户明说。
换装时只描述服装，禁止顺带描述脸和发型。
没有脸的构图（拍腿 / 拍手 / 局部）→ prompt 里写「局部特写」「第一人称视角」
把范围框住；想排除脸部用 negative_prompt 参数写「脸部, 眼睛」，不要写进 prompt。

三、要排除的东西一律走 negative_prompt
负面词写在 negative_prompt 参数里（逗号分隔），**绝对不要**写成
`prompt + " --no xxx"` —— 本地后端是 ComfyUI 的文本编码器，只认纯文本，
不认 --no 语法（那是某些前端自己的糖）。写进 prompt 会被当成正面描述，
等于主动要求模型把那些东西画出来。
常用：多余的头, 悬浮的头, 畸形, 糟糕的透视, 多手多脚, 水印, 文字, 分格, 多个角色。

四、调用前自检
1. 画「你」是否走了 persona？（含只拍腿部 / 手部这种没有脸的局部）
2. prompt 里是否出现了脸型、发色、瞳色、五官词？出现就删。
3. 是否明确写了「保留 <image1> 中的脸型和发型」？
4. 要排除的内容是否写进了 negative_prompt，而不是写成 prompt 里的 --no？"""


def build_fact_line(
    persona_name: str = "",
    ref_filename: str = "",
    candidates: list[str] | None = None,
    suggestion: str = "",
) -> str:
    """拼「当前事实」那一行 —— 模型不知道这些，就只能靠猜，猜错就退成文生图。

    刻意**不**把参考图目录路径写进来：模型不能操作文件系统，它只需要知道
    「有没有图」；一条 120 字符的绝对路径只会白占篇幅。

    Args:
        persona_name: 当前生效人格名。空串 = 没解析出来。
        ref_filename: 该人格命中的参考图文件名。空串 = 没有。
        candidates: 目录里现有的文件名（没人格图时列一两个，便于模型顺口提示用户）。
        suggestion: ``persona_refs.suggest_ref`` 给的近似名建议。

    Returns:
        一到两行文本，始终以「当前事实：」开头。
    """
    name = (persona_name or "").strip()
    fname = (ref_filename or "").strip()

    if name and fname:
        return f"当前事实：生效人格「{name}」，已有人格参考图 {fname}。"
    if name:
        line = (
            f"当前事实：生效人格「{name}」，但参考图目录里**没有**对应的图 —— "
            "画「你自己」时仍照常选 persona，插件会说明缺图。"
        )
        extra = _candidate_hint(candidates, suggestion)
        return line + extra
    line = "当前事实：没解析出当前会话人格 —— 画「你自己」时仍照常选 persona。"
    extra = _candidate_hint(candidates, suggestion)
    return line + extra


def _candidate_hint(candidates: list[str] | None, suggestion: str) -> str:
    parts: list[str] = []
    if suggestion:
        parts.append(suggestion.replace("\n", " ").strip())
    elif candidates:
        shown = "、".join(candidates[:3])
        parts.append(f"目录里现有：{shown}")
    if not parts:
        return ""
    return "（" + "；".join(parts) + "）"


# 工作流那一节的标题（接在 SYSTEM_RULES 的「三、调用前自检」之后）
WORKFLOW_HEADER = "五、选工作流（只对本地 ComfyUI 生效，云端不区分）"


def build_workflow_section(menu_text: str, local_available: bool = True) -> str:
    """拼「选工作流」那一节。

    ``menu_text`` 是插件扫目录现扫出来的清单（名字 + 用途 + 吃不吃参考图）。
    本地后端不可用时返回空串 —— 那段规则纯属噪声，白占注意力。
    """
    if not local_available or not (menu_text or "").strip():
        return ""
    return (
        f"{WORKFLOW_HEADER}\n"
        "用 workflow 参数点名，不传 = 插件按需求自己挑（推荐，除非下面有硬理由）。\n"
        "可用清单：\n"
        f"{menu_text.strip()}\n"
        "硬规则：本次要用参考图时，只能点「支持参考图」的那一套，否则参考图白给。\n"
        "点名了不存在的工作流不会静默换一套跑掉，插件会告诉你可选哪些。"
    )


EXTRA_HEADER = "【插件配置里的额外设定】"

# 「生图前先说一句」。刻意放最后 —— 它是说话风格提示而非硬规则，
# 不该挤掉前面的决策规则与工作流清单。措辞是「可以」不是「必须」：
# 插件不替模型决定这一次该不该开口。
SAY_HINT = (
    "【先说一句】调用本工具之前，你**可以**先对用户说一句话"
    "（例：「好~我画一下」「稍等，我找找灵感」）。"
    "说不说、说什么由你按当下语境自己判断 —— 画图要等几十秒，"
    "先说一句能让用户知道你在忙；但别为了说话而说话，一句话就够。"
)


def build_system_block(
    persona_name: str = "",
    ref_filename: str = "",
    candidates: list[str] | None = None,
    suggestion: str = "",
    workflow_menu: str = "",
    local_available: bool = True,
    extra: str = "",
    say: bool = True,
) -> str:
    """拼完整的一段（含动态事实）。

    ``extra`` = 用户在配置里自己填的补充设定（角色外貌、画风偏好…），原样附在
    末尾。插件刻意不内置任何具体角色 —— 那是部署方的事，写死了对别人就是噪声。
    """
    fact = build_fact_line(
        persona_name=persona_name,
        ref_filename=ref_filename,
        candidates=candidates,
        suggestion=suggestion,
    )
    parts = [SYSTEM_HEADER, fact, SYSTEM_RULES]
    section = build_workflow_section(workflow_menu, local_available)
    if section:
        parts.append(section)
    if say:
        parts.append(SAY_HINT)
    if (extra or "").strip():
        parts.append(f"{EXTRA_HEADER}\n{extra.strip()}")
    return "\n".join(parts)


def inject_into_system_prompt(system_prompt: str, block: str) -> str:
    """幂等拼接。已经在里面就不动，避免多轮请求把同一段叠好几遍。"""
    base = system_prompt or ""
    if not block:
        return base
    if SYSTEM_HEADER in base:
        return base
    if not base.strip():
        return block
    return base.rstrip() + "\n\n" + block


def block_char_count(block: str) -> int:
    """按「非空白字符」数，用来核对是否落在预期的篇幅里。"""
    return len("".join((block or "").split()))
