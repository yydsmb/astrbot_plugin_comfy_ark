"""AstrBot 豆包生图插件（独立版）—— 文生图 / 图生图（参考图）。

指令：
  /画图 [提示词]      自动判断：消息里有参考图 → 图生图；没有 → 文生图
  /生图 [提示词]      同 /画图（别名）
  /图生图 [提示词]    强制图生图（必须带参考图，否则提示）
  /文生图 [提示词]    强制文生图（忽略消息里附带的图片）
  /画图帮助           用法与当前配置

参考图来源（可单用可组合，最多 max_reference_images 张）：
  1. 当前消息里附带的图片；
  2. 你「引用 / 回复」的那条消息里的图片（Reply.chain）；
  3. 提示词里直接写出来的图片链接。

为什么默认把参考图转成 base64：
  火山方舟 images/generations 的 image 参数既接受 http 链接也接受 base64。
  但 QQ / 微信下发的图片链接往往带鉴权（方舟服务器抓不到），所以默认
  reference_transport=base64 —— 插件先把参考图落到本地再编码上传，兼容性最好。
  若你的图床是公开可访问的，可把 reference_transport 改成 url 走直链、更省流量。

「上传或不上传由用户自己选择」怎么实现：
  默认 allow_reference_image=true 时，带图就是图生图、不带图就是文生图（auto）；
  用户想在有图的情况下也走纯文生图，直接用 /文生图；想强制要参考图用 /图生图。

—— v1.0.1 修复 ——
  火山方舟 5.0 pro 模型**不支持** `sequential_image_generation` 参数（连 "disabled"
  都会返回 400 InvalidParameter）。旧代码每次请求都带上它，导致 pro 模型 100% 失败。
  现在：单张生成绝不携带该参数；只有「确实要一次多张」且模型支持组图时才加。
  另外新增「参数自适应」：上游若返回 InvalidParameter 并点名某个可选参数，
  插件会自动剔除该参数重试一次，避免以后方舟调整参数又整片挂掉。

—— v1.0.2 修复 ——
  报错说「没权限」，真相却是**账户欠费**。方舟对欠费账号返回的是：
      HTTP 403 | type=Forbidden | code=AccountOverdueError
      message: The request failed because your account has an overdue balance.
  403 + Forbidden 长得跟「权限不足」一模一样，旧代码就笼统翻成了「没有权限调用」，
  把人带偏到查 Key / 查模型开通上。现在：
    1) 先按**错误原文关键词**（overdue / arrears / 欠费 / 余额）+ 错误码识别欠费，
       并给出「去火山方舟控制台充值」这种能直接照做的提示；
    2) 报错里始终带上原始 code 与上游原文，不用再猜；
    3) 新增 `/画图诊断`：发一个**零成本**请求（故意不带 prompt，只走到鉴权与
       账户/模型校验就失败、不生成图片），一次性查出究竟是 Key 无效、账户欠费、
       模型未开通还是参数问题。

—— v1.1.0 省钱 ——
  原默认模型是 5.0 pro + 2K，这恰好是**最贵的一档**（0.60 元/张）。
  火山方舟官方价（元/张）：
    4.0        = 0.20   ← 最便宜
    5.0 / lite = 0.22
    4.5        = 0.25
    5.0 pro    = ≤1.5K 时 0.30 / >1.5K 时 0.60（唯一按像素分档的模型）
  于是把默认模型改成 **doubao-seedream-4-0-250828（0.20 元/张）**，并且：
    · 免费额度：4.0 送 200 张、4.5 送 200 张、5.0-lite 送 50 张，
      **pro 没有免费额度**（仅首张免费）；额度用完后会自动转付费，
      建议到控制台「模型算力计费」设置用量上限。
    · 只有 pro 按像素分档；4.0 / 4.5 / lite 单价与尺寸无关，开 2K/4K 不加钱。
    · 组图按实际张数计费（count>1 线性涨价）；参考图输入对 4.0/4.5/lite **免费**，
      pro 则是 0.02 元/张（首张免费）。
    · 新增两道省钱护栏：`daily_limit`（每人每天张数上限）、
      `cache_seconds`（同提示词短期内复用本地图，不再发请求、不花钱）。
    · 帮助与诊断里直接显示当前配置的**单张估算费用**、免费额度提示与今日用量。

—— v1.2.0 修「模型选了却生不出来」——
  1) 5.0-lite 在本账号下**无法生成**：它能通过模型存在性校验，但真发请求时上游返回
     `InvalidParameter: unable to decode '' as int64`（网关内部路由错误，不是参数问题）。
     已把它从配置下拉里摘掉，并在启动时对选中它的用户给出明确警告 + 替代建议。
  2) `output_format` 对 4.0 / 4.5 是**不支持的参数**（上游直接 400）。
     旧版每次都带，靠「剔除参数重试」自救，白跑一次请求、还刷警告。
     现在：用户选 jpeg（缺省格式）时**根本不发** output_format；
     只有明确要 png 且模型支持时才发。既省一次往返，也不再刷警告。
  3) 参数自适应升级：上游若报 InvalidParameter 但**没点名**是哪个参数，
     自动尝试剔除 output_format 重试一次，并把「这个模型不收 output_format」
     记在内存里，之后同一模型的请求直接不带它（自愈，不再反复试错）。

—— v1.3.0 「5.0-lite 到底能不能用」查清了 ——
  2026-09-15 用零成本探针（只带 model、不带 prompt，请求停在参数校验就失败，
  不生成图片、不产生任何费用）把「模型 ID × 接入点」矩阵扫了一遍：
    · 标准端点 /api/v3 **认得** doubao-seedream-5-0-lite-260128
      （返回 400 MissingParameter，说明是合法模型 ID，不是拼写错误）；
      但带 prompt 真发请求时 400 `unable to decode '' as int64`。
      这是「别名 → 接入点解析拿到空值」的形态，通常意味着该模型在本账号下
      **没有可用接入点（未开通）**，而不是参数 / Key / 余额的问题。
    · 专属端点 /api/plan/v3（Agent Plan / CodePlan）上，lite 与 pro 返回
      401 AuthenticationError —— 说明它们**属于 plan 模型族**（只是标准 Key 不是 plan Key），
      而 4.0 / 4.5 / 5.0 返回 404 UnsupportedModel（不属于 plan 模型族）。
      官方文档示例里 lite 的写法也正是「点号」形式 doubao-seedream-5.0-lite + plan 端点。
  因此本版做三件事：
    1) 把 lite 放回配置下拉（用户明确要用），并在启动 / 诊断 / 报错三处给出**可执行指引**：
       到控制台「系统管理 → 开通管理 → 视觉模型」开通 Seedream 5.0 lite（送 50 张免费额度）；
       若持有 CodePlan / Agent Plan，则可把 api_url 换成专属端点并配套专属 Key。
    2) 新增 `auto_fallback`（默认开）：一旦命中这种「模型无法路由」的错，
       自动用 `fallback_model`（默认 4.0）重建请求重试一次，并在结果里
       注明实际使用的模型 —— 用户永远不会空手而归，失败请求本身不计费。
    3) 报错文案不再写死「改成 4.0」，而是给出**根因 + 两条修复路径**。
    4) 新增 `api_url` 配置，便于切到 Agent Plan 专属端点。

—— v1.4.0 对照官方文档补参数 ——
  依据火山方舟官方文档「图片生成 API」（82379/1541523），逐字段核对了一遍，
  把插件缺的、错的都补齐了：
    · 能力表从 3 个字段扩成完整表：max_refs（参考图上限）、pixels（方式 2 的
      总像素区间）、web_search / fast_prompt / stream / fmt_param / layers / alpha。
      关键修正（文档明写、插件此前不符）：
        - 参考图上限 pro = 10 张，**lite / 4.5 / 4.0 = 14 张**（旧版一律按 10 卡死）；
        - 自定义宽高的总像素区间按模型分档：pro [921600, 4624220]、
          lite / 4.5 [3686400, 16777216]、4.0 [921600, 16777216]；
          宽高比一律 [1/16, 16]。旧版对自定义宽高**完全不校验**，
          用户写 512x512 会被上游 400 打回，而错误文案看不出原因。
        - output_format 文档只列了 5.0 pro 与 5.0 lite 支持，4.5 / 4.0 不支持 ——
          与 v1.2.0 的实测结论一致，能力表里补上 `fmt_param` 显式记录。
    · 新增 `web_search` 配置：下发 tools=[{"type":"web_search"}] 让模型联网
      （文档明确**仅 5.0 lite 支持**；不支持的模型自动忽略并注明）。
    · 新增 `optimize_prompt_mode` 配置：fast 模式耗时更短、质量略降
      （文档明确**仅 5.0 pro 与 4.0 支持**，lite / 4.5 传了会报错）。
      standard 是服务端默认值，所以只有选了 fast 才真的下发该字段。
    · 补上「参考图数 + 生成数 ≤ 15」的全路径校验，以及参考图按模型上限裁剪。
    · 文档提到但**本版刻意不做**的两项：layer_decomposition（图层拆分，仅 pro，
      一次吐 1 底图 + 最多 16 个 PNG 图层，往聊天窗里灌会直接刷屏）、
      background=transparent（仅 pro，且必须是 1 张带透明通道的图生图，
      和 output_format=jpeg 冲突会报错）。二者都只在 pro 上有效，
      而 pro 单价最高、且与「用 lite 省钱」的目标相悖，故留作后续按需再加。

—— v1.5.0 接入 Seedream 5.0 flash（2026-09-29 逐项实测）——
  新增模型 doubao-seedream-5-0-flash-260915：**0.12 元/张**，是目前在售模型里
  单价最低的一档（4.0 = 0.20、lite / 5.0 = 0.22、4.5 = 0.25、pro = 0.30~0.60），
  且输入参考图免费。因此把 DEFAULT_MODEL 从 4.0 换成它。
  能力表不是照抄文档，而是**每一项都实打实发请求验过**：
    · 标准端点 /api/v3 可直接用，**不需要** Agent Plan 专属端点
      （plan 端点对 flash 返回 404 UnsupportedModel，对 pro 才返回 401）。
    · 尺寸档位只有 1K / 1.5K / 2K —— 3K / 4K 一律 400
      （"must be 'WIDTHxHEIGHT' or a supported size preset"），插件会自动降到 2K。
      且 1K 档的实际长宽由提示词内容决定（实测出现过 832x1248，不是固定正方形）。
    · 自定义宽高的总像素区间 [921600, 4624220]，与 **pro 完全相同**
      （`512x512` 报 "at least 921600"，`3000x3000` 报 "at most 4624220"）。
    · 参考图上限 **10 张**（与 pro 同；lite / 4.5 / 4.0 才是 14）。
    · 输出格式 png / jpeg 都收（fmt_param = True）。
    · 图层拆分 layer_decomposition 支持（实测 1 张输入图返回 3 张：1 底图 + 2 图层）。
    · background=transparent 也认，但要求「1 张带透明通道的 PNG 输入图」，
      与 pro 的契约一致。
    · **不支持**：组图 sequential_image_generation（"not supported by this model"）、
      联网搜索 tools、流式 stream、极速提示词优化（只接受 standard）。
    · 首次使用前必须先在控制台开通，否则 404 `ModelNotOpen`。
  另外把 auto_fallback 的触发条件从「仅网关路由错误」扩到
  「模型在本账号下不可用」（网关路由错误 / ModelNotOpen / InvalidEndpointOrModel）——
  这两类是同一回事：剔参数重试毫无意义，而失败请求不计费，回退能让用户不至于空手而归。

—— v2.0.0 本地优先 + 内容审查 + 人格参考图 ——
  1) 生成后端三档（配置 backend）：
       local_first（默认）：先跑本地 ComfyUI，连不上 / 工作流报错 / 超时才走云端兜底；
       local_only：只用本地，一次都不调云端（api_key 可以留空）；
       cloud_only：保持 v1.x 的老行为，只走火山方舟。
  2) 本地 ComfyUI 接入（实测本机 127.0.0.1:8188，RTX 5070 8G，Comfy 0.38.0）：
       · 提交 POST /prompt，轮询 GET /history/{id}，取图 GET /view?...
         —— /view 的查询参数**必须 URL 编码**：工作流 filename_prefix 是中文
         （`Qwen_2.1-文生图`），不编码 http 客户端直接抛 UnicodeEncodeError（实测踩过）。
       · 参考图走 POST /upload/image（multipart）上传到 ComfyUI 的 input 目录，
         再把返回的文件名填进 LoadImage 节点。
       · 节点不写死在配置里，而是按**连线关系**从工作流反推语义
         （采样器 → positive/negative → 文本字段；latent_image → 决定尺寸来源），
         所以用户换工作流不用改配置。认不出来时明确提示「必须是导出(API) 的那份」。
       · 实测耗时：纯文生图工作流 1.6s（模型已缓存）、参考图工作流 39.3s。
  3) 内容审查（配置 moderation_*）：
       云端方舟自带审核；**本地 ComfyUI 完全没有审核** —— 而本地跑的恰恰是能出
       R18 的开源模型。所以补两道闸门：
         · 生成前查提示词（纯文本调用，很便宜；放在花钱之前，拦下就一分钱不花）
         · 生成后查图片（把生成的图交给用户指定的多模态模型看，在**发送之前**拦）
       审查模型必须由用户显式指定（moderation_provider_id，留空则用会话默认模型）。
       判定解析「宽进严出」：先按约定格式解析，再退到关键词扫描（带否定词过滤，
       「这不是 R18」不会被算命中），最后才认「审不了」—— 而「审不了」与「判为安全」
       是两件事，前者按 moderation_fail_open 决定放行还是拦截，绝不默认当安全。
  4) 人格参考图（配置 persona_reference_enabled / reference_dir）：
       参考图放插件目录的 refs/ 下，**文件名 = AI 人格名**（例如 `橙橘.png`）。
       生成时按当前会话人格匹配；同名多格式按 png > webp > jpg 取优先级最高的。
       名字对不上可在 reference_alias_map 里写一行 `人格名=文件名`。
  5) AI 自己决定用不用参考图：LLM 工具参数 reference_source
       = persona（人格图）/ message（消息附图）/ none（不用）/ auto（按配置优先级）。
  6) 参考图工作流报错时**自动退回纯文本工作流**重新生成（comfyui_ref_fallback_text），
       而不是让整次请求失败 —— 这是用户明确要求的行为。
  7) 省钱护栏语义微调：daily_limit 只对**云端**生效（本地生成不花钱，不该被额度卡住）；
       rate_limit / max_concurrent 两道对两条链路都生效。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import time
import uuid
from datetime import datetime
from typing import Any

import aiohttp

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.star.filter.command import GreedyStr

from .comfyui_backend import (
    DEFAULT_COMFY_URL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TIMEOUT,
    OutputImage,
    WorkflowSpec,
    apply_params,
    clamp_int,
    detect_workflow,
    explain_exception,
    explain_http_error,
    extract_images,
    history_endpoint,
    history_status,
    missing_node_types,
    normalize_comfy_url,
    object_info_endpoint,
    parse_object_info,
    prompt_endpoint,
    queue_endpoint,
    safe_float,
    summarize_queue,
    summarize_system_stats,
    system_stats_endpoint,
    upload_endpoint,
)
from .image_refs import (
    bytes_to_data_uri,
    extract_image_urls_from_text,
    local_path_from_ref,
    looks_like_base64,
    readable_size,
    sanitize_ref_for_log,
    sniff_image_mime,
    strip_command_prefix,
    truncate_prompt,
)
from .moderation import (
    Verdict,
    build_image_check_system,
    build_image_check_user,
    build_prompt_check_system,
    build_prompt_check_user,
    parse_verdict,
    unclear_receipt,
    verdict_receipt,
)
from .persona_refs import (
    describe_refs,
    list_ref_files,
    parse_alias_map,
    resolve_ref,
    scan_ref_dir,
    suggest_text,
)
from .prompt_guide import (
    build_system_block,
    inject_into_system_prompt,
)
from .slot_tokens import (
    BACKEND_QWEN as SLOT_BACKEND_QWEN,
)
from .slot_tokens import (
    BACKEND_SEEDREAM as SLOT_BACKEND_SEEDREAM,
)
from .slot_tokens import (
    adapt_slots,
)
from .tool_args import (
    CANONICAL_REF_PARAM,
    CANONICAL_WORKFLOW_PARAM,
    REAL_PARAMS,
    coerce_int,
    coerce_size,
    coerce_str,
    describe_arg_fixes,
    normalize_tool_args,
)
from .workflows import (
    WorkflowEntry,
    describe_for_llm as describe_workflows_for_llm,
    match as match_workflow,
    scan_dir as scan_workflow_dir,
)

PLUGIN_NAME = "astrbot_plugin_comfy_ark"
PLUGIN_VERSION = "3.1.0"
API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
# Agent Plan / CodePlan 专属端点：模型名用「点号」形式（doubao-seedream-5.0-lite），
# 且必须配套 plan 专属 API Key；拿普通 Key 访问会返回 401 AuthenticationError。
AGENT_PLAN_URL = "https://ark.cn-beijing.volces.com/api/plan/v3/images/generations"
LLM_TOOL_NAME = "comfy_ark_generate_image"

# ----------------------------------------------------------------------
# 生成后端（v2.0.0）
#   local_first ：本地 ComfyUI 优先，失败再走云端（默认）
#   local_only  ：只用本地，一次都不调云端（api_key 可以留空）
#   cloud_only  ：只用云端火山方舟（= v1.x 的老行为）
# ----------------------------------------------------------------------
BACKEND_LOCAL_FIRST = "local_first"
BACKEND_LOCAL_ONLY = "local_only"
BACKEND_CLOUD_ONLY = "cloud_only"
BACKENDS = (BACKEND_LOCAL_FIRST, BACKEND_LOCAL_ONLY, BACKEND_CLOUD_ONLY)
BACKEND_ZH = {
    BACKEND_LOCAL_FIRST: "本地优先（失败走云端）",
    BACKEND_LOCAL_ONLY: "只用本地",
    BACKEND_CLOUD_ONLY: "只用云端",
}
BACKEND_LOCAL = "本地 ComfyUI"
BACKEND_CLOUD = "云端豆包"

# ----------------------------------------------------------------------
# 参考图来源（LLM 工具参数 reference_source）
#   auto    ：按 reference_priority 自动挑（默认人格图优先）
#   persona ：只用「当前 AI 人格」的参考图
#   message ：只用消息里附带的图 / 引用的图 / 提示词里的图链
#   none    ：完全不用参考图
# ----------------------------------------------------------------------
# 这些 persona_id 等于「没人格」，不能拿去当参考图的文件名
_PERSONA_IGNORE = ("[%None]", "default", "_chatui_default_", "默认")

REF_SOURCE_AUTO = "auto"
REF_SOURCE_PERSONA = "persona"
REF_SOURCE_MESSAGE = "message"
REF_SOURCE_NONE = "none"
REF_SOURCES = (REF_SOURCE_AUTO, REF_SOURCE_PERSONA, REF_SOURCE_MESSAGE, REF_SOURCE_NONE)

# 本地工作流自带文件（配置留空时用它们）
BUNDLED_T2I_WORKFLOW = "qwen_t2i.json"
BUNDLED_REF_WORKFLOW = "qwen_ref.json"

# 「可选工作流」目录：AI 能自己挑的预设都放这里（相对插件根目录）。
# 目录即清单 —— 丢一个 *.json（必须 API 格式）进去就自动进入 AI 的候选；
# 同名 .txt 的首行【名称】当显示名，余下当用途说明，供模型判断何时该用它。
PRESET_SUBDIR = ("workflows", "presets")

# 工作流的两种角色。由 detect_workflow 按连线反推，**不看文件名**。
KIND_REF = "ref"  # 能吃参考图
KIND_T2I = "t2i"  # 只能文生图

# 官方口径：输入参考图数 + 最终生成图片数 ≤ 15
TOTAL_IMAGE_LIMIT = 15
# 参考图张数的绝对天花板（配置再大也不会超过）。
# 注意各模型真实上限不同：5.0 pro 是 10 张，5.0 lite / 4.5 / 4.0 是 14 张，
# 这里取两者中的大值，真正的裁剪在下游按 MODEL_CAPS["max_refs"] 逐模型做。
REF_HARD_LIMIT = 14
# 单张参考图体积硬上限（官方：不超过 30MB）
REF_HARD_BYTES = 30 * 1024 * 1024
# 宽高比允许范围（官方：所有模型均为 [1/16, 16]）
ASPECT_MIN = 1 / 16
ASPECT_MAX = 16.0

DEFAULT_I2I_PROMPT = "以参考图为基础，保持主体特征与构图一致，生成一张更高质量的图片"

SESSION_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=20)
TEMP_RETENTION = 3600  # 本地临时图保留 1 小时

DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
}

# 已知的指令词（用于从消息里回捞提示词时剥离）
KNOWN_COMMANDS = ("画图帮助", "画图", "生图", "图生图", "文生图")

# 出问题时允许被自动剔除的「可选」参数（绝不剔除 model / prompt）
OPTIONAL_PARAMS = (
    "sequential_image_generation_options",
    "sequential_image_generation",
    "output_format",
    "response_format",
    "watermark",
    "size",
    "seed",
)
# 参数自适应最多重试几次（防止死循环）
MAX_PARAM_REPAIRS = 4

# ----------------------------------------------------------------------
# 上游「接入点解析失败」的特征串
#   现象：模型**能通过存在性校验**（不带 prompt 时返回 400 MissingParameter），
#         但带 prompt 真发请求时网关内部解析失败：
#         InvalidParameter: unable to decode '' as int64: strconv.ParseInt: parsing "": invalid syntax
#   含义：别名 → 接入点的解析拿到了空值。最常见的两种原因是
#         (a) 该模型在本账号下**没有可用接入点（未开通）**；
#         (b) 该模型属于 Agent Plan 族，需要用 plan 专属端点 + 专属 Key。
#   与用户的参数、Key 余额都无关。
#   2026-09-15 实测：doubao-seedream-5-0-lite-260128 在标准端点必失败，
#   且与是否携带 output_format / sequential_image_generation 无关。
# ----------------------------------------------------------------------
# 只保留「网关内部解析失败」的高特征串。
# ⚠️ 绝对不要把上游那句通用前缀 "One or more parameters specified in the request
#    are not valid" 放进来 —— 拒绝 output_format 时也是这句开头，会把它误判成
#    「模型不可用」，把用户往错误方向带。
ROUTING_ERROR_MARKERS = ("unable to decode", "strconv.parseint")

# 已知会出现「接入点解析失败」的模型 → 建议的替代模型（保底能出图的那个）
MODEL_UNUSABLE: dict[str, str] = {
    # 替代建议指向 5.0 flash：单价全系最低（0.12 元/张）、标准端点可直接用，
    # 能力面覆盖 lite 的绝大部分场景（只少了组图与联网搜索）。
    "doubao-seedream-5-0-lite-260128": "doubao-seedream-5-0-flash-260915",
}

# 已确认属于 Agent Plan / CodePlan 模型族的模型（标准 Key 走标准端点会 401）
# 依据：2026-09-15 在 /api/plan/v3 上实测 —— 这些模型返回 401 AuthenticationError
# （= 通过了 plan 的「模型是否受支持」校验），而 4.0/4.5/5.0 返回 404 UnsupportedModel。
AGENT_PLAN_MODELS = {
    "doubao-seedream-5-0-lite-260128",
    "doubao-seedream-5.0-lite",
    "doubao-seedream-5-0-pro-260628",
    "doubao-seedream-5.0-pro",
}
# 注意：5.0 flash **不在**上面这个集合里。2026-09-29 实测它对 plan 端点返回
# 404 UnsupportedModel（= 明确不支持 plan），而对 pro 才返回 401（= plan 认得它）。
# 即 flash 只能走标准端点，好在标准端点直接可用、不需要开 plan。

# 开通指引（多处复用，保证文案一致）
ACTIVATION_HINT = (
    "请到火山方舟控制台「系统管理 → 开通管理 → 视觉模型」开通该模型"
    "（5.0 lite 送 50 张免费额度；开通后立即生效，插件无需改动）"
)


class ModelRoutingError(RuntimeError):
    """模型无法路由到可用接入点（未开通 / 需要 Agent Plan 专属端点）。

    单独成类，是为了让上层能「识别 + 自动回退」，而不是把错误文案当控制流。
    """

    def __init__(self, message: str, model: str = ""):
        super().__init__(message)
        self.model = model


CUSTOM_SIZE_RE = re.compile(r"^\d{2,5}x\d{2,5}$", re.IGNORECASE)

# ----------------------------------------------------------------------
# 模型能力表
# 数据来源：火山方舟官方文档「图片生成 API」82379/1541523（2026-09-15 逐字段核对）
#   seq        : 是否支持「组图」(sequential_image_generation)
#   sizes      : 支持的分辨率档位；None = 不限制（自定义模型时透传）
#   formats    : 支持的输出格式；None = 不限制
#   max_refs   : 单次最多可传的参考图张数（pro=10，其余=14）
#   pixels     : 自定义宽高（方式 2）时的总像素允许区间
#   web_search : 是否支持 tools=[{type:"web_search"}] 联网搜索
#   fast_prompt: 是否支持 optimize_prompt_options.mode="fast"
#   stream     : 是否支持 stream=true 流式输出
#   fmt_param  : 是否收 output_format 参数（文档只列了 5.0 pro 与 5.0 lite）
#   layers     : 是否支持 layer_decomposition 图层拆分（仅 5.0 pro）
#   alpha      : 是否支持 background=transparent 透明通道（仅 5.0 pro）
# ----------------------------------------------------------------------
MODEL_CAPS: dict[str, dict[str, Any]] = {
    "doubao-seedream-5-0-pro-260628": {
        "seq": False,
        "sizes": {"1K", "1.5K", "2K"},
        "formats": {"png", "jpeg"},
        "max_refs": 10,
        "pixels": (921_600, 4_624_220),
        "web_search": False,
        "fast_prompt": True,
        "stream": False,
        "fmt_param": True,
        "layers": True,
        "alpha": True,
    },
    "doubao-seedream-5-0-flash-260915": {
        # 2026-09-29 逐项实测（不是照抄文档）。flash 与 pro 同属「5.0 新档」，
        # 规格高度重合：尺寸只到 2K、参考图上限 10、像素区间与 pro 相同，
        # 但**不支持组图 / 联网 / 流式 / 极速优化**。
        "seq": False,
        "sizes": {"1K", "1.5K", "2K"},
        "formats": {"png", "jpeg"},
        "max_refs": 10,
        "pixels": (921_600, 4_624_220),
        "web_search": False,
        "fast_prompt": False,
        "stream": False,
        "fmt_param": True,
        "layers": True,
        "alpha": True,
    },
    "doubao-seedream-5-0-260128": {
        # 文档未单列这一档，按 5.0 lite 同规格保守处理
        "seq": True,
        "sizes": {"2K", "3K", "4K"},
        "formats": {"png", "jpeg"},
        "max_refs": 14,
        "pixels": (3_686_400, 16_777_216),
        "web_search": True,
        "fast_prompt": False,
        "stream": True,
        "fmt_param": True,
        "layers": False,
        "alpha": False,
    },
    "doubao-seedream-5-0-lite-260128": {
        "seq": True,
        "sizes": {"2K", "3K", "4K"},
        "formats": {"png", "jpeg"},
        "max_refs": 14,
        "pixels": (3_686_400, 16_777_216),
        "web_search": True,
        "fast_prompt": False,
        "stream": True,
        "fmt_param": True,
        "layers": False,
        "alpha": False,
    },
    "doubao-seedream-4-5-251128": {
        "seq": True,
        "sizes": {"2K", "4K"},
        "formats": {"jpeg"},
        "max_refs": 14,
        "pixels": (3_686_400, 16_777_216),
        "web_search": False,
        "fast_prompt": False,
        "stream": True,
        "fmt_param": False,
        "layers": False,
        "alpha": False,
    },
    "doubao-seedream-4-0-250828": {
        "seq": True,
        "sizes": {"1K", "2K", "4K"},
        "formats": {"jpeg"},
        "max_refs": 14,
        "pixels": (921_600, 16_777_216),
        "web_search": False,
        "fast_prompt": True,
        "stream": True,
        "fmt_param": False,
        "layers": False,
        "alpha": False,
    },
}
# 未知/自定义模型：最保守 —— 不发组图/联网/优化参数，尺寸与格式透传
UNKNOWN_CAPS: dict[str, Any] = {
    "seq": False,
    "sizes": None,
    "formats": None,
    "max_refs": REF_HARD_LIMIT,
    "pixels": None,
    "web_search": False,
    "fast_prompt": False,
    "stream": False,
    "fmt_param": True,
    "layers": False,
    "alpha": False,
}

# 兜底尺寸（各模型都支持）
SAFE_SIZE = "2K"

# ----------------------------------------------------------------------
# 省钱相关：默认模型 / 单价 / 免费额度
# 数据来源：火山方舟官方「模型价格」页与模型广场（2026-09 核对）
#   · pro 是唯一按像素分档的模型：≤261 万像素(1.5K 及以下) 0.30 元/张，
#     >261 万像素(如 2K) 0.60 元/张 —— 同一个提示词用 2K 就是双倍价钱。
#   · 4.0 / 4.5 / lite 单价与尺寸**无关**，开 2K/4K 不会加钱。
#   · 组图按实际生成张数计费；参考图输入对 4.0/4.5/lite 免费，pro 收 0.02 元/张。
#   · 审核未通过的图不计费。
# ----------------------------------------------------------------------
# 2026-09-29 换成 5.0 flash：0.12 元/张，全系最低，且输入参考图免费。
# 4.0 单价 0.20 略贵，但**送 200 张免费额度**，所以它更适合当 fallback_model
# —— 新账号拿它先把 200 张白嫖掉，比按 0.12 计费更划算。
DEFAULT_MODEL = "doubao-seedream-5-0-flash-260915"

PRICE_CNY_PER_IMAGE: dict[str, float] = {
    # 官方「模型价格」页 2026-09-29 核对：flash 输出 0.12 元/张、输入图免费，
    # 是全系最低（pro 0.30~0.60 / 4.5 0.25 / lite·5.0 0.22 / 4.0 0.20）。
    "doubao-seedream-5-0-flash-260915": 0.12,
    "doubao-seedream-4-0-250828": 0.20,
    "doubao-seedream-5-0-260128": 0.22,
    "doubao-seedream-5-0-lite-260128": 0.22,
    "doubao-seedream-4-5-251128": 0.25,
}
# pro 的像素分档单价（唯一分档的模型）
PRO_PRICE_TIERS = {"small": 0.30, "large": 0.60}
PRO_REF_PRICE = 0.02  # pro 的参考图输入单价（首张免费）
DEFAULT_PRICE = 0.30  # 未知/自定义模型按中位价估算

# pro 属于「高像素档」的尺寸写法
PRO_LARGE_SIZES = {"2K", "3K", "4K"}

# 官方免费额度（张）。pro **没有**免费额度，只有「首张免费」。
FREE_QUOTA: dict[str, int] = {
    "doubao-seedream-4-0-250828": 200,
    "doubao-seedream-4-5-251128": 200,
    "doubao-seedream-5-0-lite-260128": 50,
    "doubao-seedream-5-0-260128": 50,
}

# 接口错误码 → 中文说明
# 顺序很重要：dict 是按插入顺序遍历的，「账户状态」类必须排在最前。
# 因为欠费在方舟上表现为 403 + type=Forbidden + code=AccountOverdueError，
# 长得和「没权限」一模一样，排在后面就会被误翻成权限问题。
ERROR_MAP = {
    # ---- 账户状态（欠费 / 余额 / 额度）----
    "AccountOverdueError": "账户已欠费（余额不足），请到火山方舟控制台充值后重试",
    "AccountOverdue": "账户已欠费（余额不足），请到火山方舟控制台充值后重试",
    "ArrearageError": "账户已欠费（余额不足），请到火山方舟控制台充值后重试",
    "InsufficientBalance": "账户余额不足，请到火山方舟控制台充值后重试",
    "BalanceNotEnough": "账户余额不足，请到火山方舟控制台充值后重试",
    "QuotaExceeded": "账户额度已用尽（含免费额度），请充值或更换账号后重试",
    "FreeQuotaExceeded": "免费额度已用尽，请到火山方舟控制台充值后重试",
    # ---- 鉴权 ----
    "AuthenticationError": "API Key 无效或已失效，请到火山方舟控制台「API Key 管理」重新获取",
    "Unauthorized": "API Key 无效或已失效，请到火山方舟控制台「API Key 管理」重新获取",
    # ---- 模型权限 ----
    "ModelNotOpen": (
        "该模型尚未在本账号开通。请到火山方舟控制台"
        "「系统管理 → 开通管理 → 视觉模型」开通后重试（开通即时生效，插件无需改动）"
    ),
    "InvalidEndpointOrModel": (
        "模型 ID 不存在，或该模型未在本账号开通"
        "（可到控制台「开通管理 → 视觉模型」核对已开通清单）"
    ),
    "InvalidParameter.UnsupportedModel": "模型 ID 不存在，或该模型未在本账号开通",
    "Forbidden": "请求被拒绝：多半是账户欠费或模型未开通，请用 /画图诊断 定位",
    "AccessDenied": "请求被拒绝：多半是账户欠费或模型未开通，请用 /画图诊断 定位",
    "PermissionDenied": "请求被拒绝：多半是账户欠费或模型未开通，请用 /画图诊断 定位",
    # ---- 参数 / 频率 / 服务 ----
    "MissingParameter": "请求缺少必要参数",
    "InvalidParameter": "请求参数不合法",
    "TooManyRequests": "调用频率超限，请稍后再试",
    "RateLimitExceeded": "调用频率超限，请稍后再试",
    "InternalServiceError": "上游服务异常，请稍后重试",
    "ServerError": "上游服务异常，请稍后重试",
    "ServiceUnavailable": "上游服务暂不可用，请稍后重试",
    # ---- 内容安全 ----
    "ContentPolicyViolation": "提示词或参考图触发了内容安全审核，请修改后重试",
    "SensitiveContentDetected": "提示词或参考图触发了内容安全审核，请修改后重试",
    "InputImageSensitiveContentDetected": "参考图触发了内容安全审核，请换一张再试",
    "ImageSizeInvalid": "参考图尺寸不合法（过小或过大）",
    "BadRequest": "请求被拒绝",
}

# 「欠费 / 余额不足」关键词。
# 为什么错误码之外还要按原文兜底：错误码会随方舟版本调整，但这几个词很稳定；
# 而 403 + Forbidden 这个组合既可能是欠费、也可能是模型未开通，
# 只有上游原文能分辨，所以必须先按原文判一次。
ARREARS_KEYWORDS = (
    "overdue",
    "arrear",
    "insufficient balance",
    "not enough balance",
    "insufficient funds",
    "欠费",
    "余额不足",
    "账户余额不足",
)

# 免费额度相关关键词（和「真·欠费」区分开，提示略有不同）
QUOTA_KEYWORDS = (
    "free quota",
    "quota exceeded",
    "exceeded your quota",
    "免费额度",
    "额度已用尽",
)


def looks_like_arrears(code: str, message: str, status: int = 0) -> bool:
    """判断这次失败是不是「账户欠费 / 余额不足」。

    先认错误码，再认上游原文关键词。403 单独出现时**不算**（证据不足，
    也可能是模型未开通），但 403 且原文带欠费关键词 → 一定算。
    额度用尽由 looks_like_quota() 单独负责，不在这里混判。
    """
    c = (code or "").lower()
    m = (message or "").lower()
    if any(
        k in c for k in ("overdue", "arrear", "insufficientbalance", "balancenotenough")
    ):
        return True
    return any(k in m for k in ARREARS_KEYWORDS)


def looks_like_quota(code: str, message: str) -> bool:
    """额度用尽（含免费额度）—— 处理建议和「欠费」相同，但文案要区分。"""
    blob = f"{code or ''} {message or ''}".lower()
    return any(k in blob for k in QUOTA_KEYWORDS)


def resolve_model_caps(model: str) -> dict[str, Any]:
    """按模型 ID 取能力表；支持模糊匹配（自定义版本号也能命中同系列）。

    版本号先归一化：把「点号」写法（doubao-seedream-5.0-flash —— Agent Plan
    端点用的是这种）折成「连字符」写法再匹配，否则 `"5-0" in m` 会漏掉点号形式，
    一漏就会掉进 UNKNOWN_CAPS 那种「什么都不支持」的保守档。
    """
    m = (model or "").strip().lower()
    if m in MODEL_CAPS:
        return MODEL_CAPS[m]
    n = m.replace(".", "-")
    if "seedream" in n:
        # flash 必须排在通用 "5-0" 之前：它和 5.0 / lite 的能力**不一样**
        # （不支持组图/联网/流式/极速优化，尺寸只到 2K，参考图上限 10）。
        if "5-0" in n and "flash" in n:
            return MODEL_CAPS["doubao-seedream-5-0-flash-260915"]
        if "5-0" in n and "pro" in n:
            return MODEL_CAPS["doubao-seedream-5-0-pro-260628"]
        if "5-0" in n:
            return MODEL_CAPS["doubao-seedream-5-0-260128"]
        if "4-5" in n:
            return MODEL_CAPS["doubao-seedream-4-5-251128"]
        if "4-0" in n:
            return MODEL_CAPS["doubao-seedream-4-0-250828"]
    return UNKNOWN_CAPS


def is_pro_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return "seedream" in m and "pro" in m


def is_large_size_for_pro(size: str) -> bool:
    """pro 的 2K/3K/4K（或 >261 万像素）属于 0.60 元的高像素档。"""
    s = (size or "").strip().upper()
    if s in PRO_LARGE_SIZES:
        return True
    if CUSTOM_SIZE_RE.match(s):
        try:
            w, h = (int(x) for x in s.lower().split("x"))
        except ValueError:
            return False
        return w * h > 2_610_000
    return False


def parse_custom_size(size: str) -> tuple[int, int] | None:
    """把 '2048x1024' 解析成 (2048, 1024)；不是自定义尺寸写法则返回 None。"""
    s = (size or "").strip().lower()
    if not CUSTOM_SIZE_RE.match(s):
        return None
    try:
        w, h = (int(x) for x in s.split("x"))
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return w, h


def check_custom_size(size: str, caps: dict[str, Any]) -> str:
    """校验自定义宽高是否落在该模型的像素区间与宽高比范围内。

    返回空串 = 合法；否则返回一句人话解释，用于告诉用户为什么被换掉。
    官方口径（文档 82379/1541523「size → 方式 2」）：
      · 宽高比（宽/高）必须落在 [1/16, 16]
      · 总像素（宽×高）必须落在各模型自己的区间：
          pro        [921600, 4624220]
          lite / 4.5 [3686400, 16777216]
          4.0        [921600, 16777216]
    注意总像素是对「宽×高」的乘积限制，不是对宽或高单独设限 ——
    所以 1500x1500（225 万像素）对 lite 无效，尽管它的宽高比是合法的 1:1。
    """
    wh = parse_custom_size(size)
    if wh is None:
        return ""
    w, h = wh
    ratio = w / h
    if not (ASPECT_MIN <= ratio <= ASPECT_MAX):
        return f"宽高比 {w}/{h} = {ratio:.2f}，超出允许范围 [1/16, 16]"
    rng = caps.get("pixels")
    if rng:
        low, high = rng
        px = w * h
        if not (low <= px <= high):
            return f"总像素 {px}（{w}×{h}）超出该模型允许区间 [{low}, {high}]"
    return ""


def unit_price(model: str, size: str = "", n_refs: int = 0) -> float:
    """估算单张图的单价（元）。

    pro 是唯一按像素分档的模型，且参考图第 2 张起每张 0.02 元；
    其余模型单价与尺寸无关、参考图输入免费。
    """
    m = (model or "").strip().lower()
    if is_pro_model(m):
        base = (
            PRO_PRICE_TIERS["large"]
            if is_large_size_for_pro(size)
            else PRO_PRICE_TIERS["small"]
        )
        base += PRO_REF_PRICE * max(0, n_refs - 1)
        return round(base, 4)
    if m in PRICE_CNY_PER_IMAGE:
        return PRICE_CNY_PER_IMAGE[m]
    for key, price in PRICE_CNY_PER_IMAGE.items():
        if key == m or key.startswith(m) or m in key:
            return price
    return DEFAULT_PRICE


def free_quota_of(model: str) -> int:
    """该模型的官方免费额度（张）。pro 为 0（仅首张免费）。"""
    m = (model or "").strip().lower()
    if is_pro_model(m):
        return 0
    for key, quota in FREE_QUOTA.items():
        if key == m or key.startswith(m) or m in key:
            return quota
    return 0


@register(
    PLUGIN_NAME,
    "WorkBuddy",
    "火山方舟（豆包 Doubao Seedream）文生图 / 图生图插件。支持上传参考图（消息附图、"
    "引用消息里的图、提示词里的图片链接）也可以不带参考图，由用户自行选择；"
    "支持 /画图 /生图 /图生图 /文生图 指令，并注册为 LLM 工具让 AI 在对话中自主生图。",
    PLUGIN_VERSION,
)
class ComfyArkImagePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config or {}
        cfg = self.config

        # ---------- 接口 ----------
        self.api_key = str(cfg.get("api_key") or "").strip()
        self.model = str(cfg.get("model") or DEFAULT_MODEL).strip()
        # 接入点：默认标准端点；持有 CodePlan / Agent Plan 时可换成专属端点
        # （此时模型名用「点号」写法，例如 doubao-seedream-5.0-lite）。
        self.api_url = str(cfg.get("api_url") or API_URL).strip() or API_URL
        # 模型路由失败时的兜底：自动换 fallback_model 重建请求重试一次。
        # 失败请求不计费，所以这一步不会多花钱，但能保证用户不会空手而归。
        self.auto_fallback = bool(cfg.get("auto_fallback", True))
        self.fallback_model = str(cfg.get("fallback_model") or DEFAULT_MODEL).strip()
        self.size = str(cfg.get("size") or SAFE_SIZE).strip()
        self.output_format = str(cfg.get("output_format") or "jpeg").strip().lower()
        self.watermark = bool(cfg.get("watermark", False))
        self.count = max(1, int(cfg.get("count", 1) or 1))
        self.seed = int(cfg.get("seed", -1) or -1)
        # 联网搜索（tools=[{type:"web_search"}]）—— 文档明确仅 5.0 lite 支持。
        # 开启后模型会自行判断是否联网（商品、天气、时事这类时效性提示词才用得上），
        # 代价是时延上升、且可能引入额外调用，所以默认关闭。
        self.web_search = bool(cfg.get("web_search", False))
        # 提示词优化模式：standard = 质量优先（服务端默认）；fast = 耗时更短、质量略降。
        # 文档明确 fast 目前只有 5.0 pro 与 4.0 支持，lite / 4.5 传了会报错。
        # 由于 standard 本身就是服务端默认值，所以只有选了 fast 才真的下发这个字段。
        _mode = str(cfg.get("optimize_prompt_mode") or "standard").strip().lower()
        self.optimize_prompt_mode = (
            _mode if _mode in ("standard", "fast") else "standard"
        )

        # ---------- 参考图 ----------
        self.allow_reference = bool(cfg.get("allow_reference_image", True))
        self.reference_transport = (
            str(cfg.get("reference_transport") or "base64").strip().lower()
        )
        self.max_reference_images = max(
            1, min(REF_HARD_LIMIT, int(cfg.get("max_reference_images", 4) or 4))
        )
        self.max_reference_mb = max(1.0, float(cfg.get("max_reference_mb", 8) or 8))
        self.default_reference_prompt = (
            str(cfg.get("default_reference_prompt") or DEFAULT_I2I_PROMPT).strip()
            or DEFAULT_I2I_PROMPT
        )

        # ---------- 生成后端 ----------
        _backend = str(cfg.get("backend") or BACKEND_LOCAL_FIRST).strip().lower()
        self.backend = _backend if _backend in BACKENDS else BACKEND_LOCAL_FIRST

        # ---------- 本地 ComfyUI ----------
        self.comfy_url = normalize_comfy_url(
            cfg.get("comfyui_url") or DEFAULT_COMFY_URL
        )
        self.comfy_timeout = max(
            30.0, safe_float(cfg.get("comfyui_timeout"), DEFAULT_TIMEOUT)
        )
        self.comfy_poll_interval = max(
            0.5, safe_float(cfg.get("comfyui_poll_interval"), DEFAULT_POLL_INTERVAL)
        )
        self.comfy_width = clamp_int(cfg.get("comfyui_width"), 256, 4096, 1024)
        self.comfy_height = clamp_int(cfg.get("comfyui_height"), 256, 4096, 1024)
        # Qwen Image 2.1 的 resolution：参考图会被缩放到约 resolution×resolution（0 = 保持原尺寸）
        self.comfy_resolution = clamp_int(cfg.get("comfyui_resolution"), 0, 4096, 1024)
        # 步数：0 = 不覆盖，沿用工作流 JSON 里自带的值。精准版 35 步、美感版 25 步
        # 本来就不一样，用一把统一的尺子去量会把各自调好的参数抹平。
        self.comfy_steps = clamp_int(cfg.get("comfyui_steps"), 0, 100, 0)
        self.comfy_negative = str(cfg.get("comfyui_negative_prompt") or "").strip()
        # 参考图工作流失败时是否退回纯文本工作流（用户明确要的行为）
        self.comfy_ref_fallback = bool(cfg.get("comfyui_ref_fallback_text", True))
        # 工作流路径留空 = 用插件自带的那两个
        self.comfy_t2i_workflow = str(cfg.get("comfyui_t2i_workflow") or "").strip()
        self.comfy_ref_workflow = str(cfg.get("comfyui_ref_workflow") or "").strip()

        # ---------- 可选工作流：让 AI 自己挑（v3.1.0）----------
        # 目录留空 = 插件自带的 workflows/presets/。目录即清单，不写配置。
        _wf_dir = str(cfg.get("comfyui_workflow_dir") or "").strip()
        self.comfy_workflow_dir = _wf_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), *PRESET_SUBDIR
        )
        # 关掉 = 不把清单交给模型，永远按 ref 需求自动挑（旧行为）
        self.comfy_workflow_select = bool(cfg.get("comfyui_workflow_select", True))
        # AI 没指定时优先用哪个（按名字匹配，找不到就自动挑）
        self.comfy_default_workflow = str(
            cfg.get("comfyui_default_workflow") or ""
        ).strip()
        # (mtime, entries) —— 目录内容变了才重扫，避免每次生图都读盘
        self._wf_cache: tuple[float, list[WorkflowEntry]] = (-1.0, [])
        # path -> (wf, spec, err) —— 单个预设的加载结果，进程内缓存
        self._entry_cache: dict[str, tuple[dict | None, WorkflowSpec | None, str]] = {}

        # ---------- 人格参考图 ----------
        _ref_dir = str(cfg.get("reference_dir") or "").strip()
        self.reference_dir = _ref_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "refs"
        )
        self.persona_reference_enabled = bool(
            cfg.get("persona_reference_enabled", True)
        )
        self.reference_priority = (
            "message"
            if str(cfg.get("reference_priority") or "persona").strip().lower()
            == "message"
            else "persona"
        )
        self.reference_aliases = parse_alias_map(cfg.get("reference_alias_map"))
        self.persona_name_default = str(cfg.get("persona_name_default") or "").strip()
        self._ref_cache: tuple[float, dict[str, str]] = (-1.0, {})

        # ---------- 内容审查 ----------
        # 云端有上游审核；本地没有，所以本地生成必须自己挡一道。
        self.moderation_enabled = bool(cfg.get("moderation_enabled", True))
        # 识图模型：填 Provider ID。留空 = 用当前会话的默认对话模型。
        self.moderation_provider_id = str(
            cfg.get("moderation_provider_id") or ""
        ).strip()
        self.moderation_check_prompt = bool(cfg.get("moderation_check_prompt", True))
        # v2.0.3 提示词层：把「生图前自查」注入 system_prompt。
        # 注入路径每条消息都要跑，而解析人格要读库，所以带一层 30 秒缓存。
        self.prompt_guide_enabled = bool(cfg.get("prompt_guide_enabled", True))
        self._guide_cache: dict[str, Any] = {}
        self.moderation_check_image = bool(cfg.get("moderation_check_image", True))
        # 拿不准时按不安全处理（严格）；默认宽松，避免误杀正常图
        self.moderation_strict = bool(cfg.get("moderation_strict", False))
        # 审查模型调不通 / 答不出判定时：True = 放行（默认），False = 拦截
        self.moderation_fail_open = bool(cfg.get("moderation_fail_open", True))
        self.moderation_timeout = max(
            5.0, safe_float(cfg.get("moderation_timeout"), 60.0)
        )
        self.moderation_max_chars = clamp_int(
            cfg.get("moderation_prompt_max_chars"), 100, 8000, 2000
        )

        # ---------- 提示词 ----------
        self.prompt_prefix = str(cfg.get("prompt_prefix") or "").strip()
        self.prompt_suffix = str(cfg.get("prompt_suffix") or "").strip()

        # ---------- 回执 ----------
        self.show_prompt = bool(cfg.get("show_prompt", False))
        self.success_text = str(cfg.get("success_text") or "").strip()

        # ---------- 稳定性 ----------
        self.download_locally = bool(cfg.get("download_locally", True))
        self.rate_limit = max(0, int(cfg.get("rate_limit_seconds", 8) or 0))
        self.max_concurrent = max(1, int(cfg.get("max_concurrent", 2) or 2))
        self.retry_times = max(0, int(cfg.get("retry_times", 1) or 0))

        # ---------- 省钱护栏 ----------
        # 每人每天张数上限（0 = 不限）。超过就拒掉，钱包不会再被刷爆。
        self.daily_limit = max(0, int(cfg.get("daily_limit", 30) or 0))
        # 同提示词 N 秒内复用本地已生成的图，直接重发、不再请求上游（0 = 关闭）
        self.cache_seconds = max(0, int(cfg.get("cache_seconds", 0) or 0))

        # ---------- 模型能力 ----------
        self.caps = resolve_model_caps(self.model)
        self.supports_group = bool(self.caps.get("seq"))
        self.caps_sizes = self.caps.get("sizes")
        self.caps_formats = self.caps.get("formats")
        # 参考图张数的模型级上限（pro 10 / lite·4.5·4.0 14）
        self.caps_max_refs = int(self.caps.get("max_refs") or REF_HARD_LIMIT)
        # 配置里的上限再大，也不能超过模型的真实上限
        self.max_reference_images = min(self.max_reference_images, self.caps_max_refs)
        self.supports_web_search = bool(self.caps.get("web_search"))
        self.supports_fast_prompt = bool(self.caps.get("fast_prompt"))

        # ---------- 启动自检：配置与模型能力不符时给出可读警告 ----------
        if self.caps_sizes is not None and not CUSTOM_SIZE_RE.match(self.size):
            if self.size.upper() not in {s.upper() for s in self.caps_sizes}:
                logger.warning(
                    f"[{PLUGIN_NAME}] 模型 {self.model} 不支持尺寸 {self.size}，"
                    f"将自动降级为 {SAFE_SIZE}（支持：{'/'.join(sorted(self.caps_sizes))}）。"
                )
        if (
            self.caps_formats is not None
            and self.output_format not in self.caps_formats
        ):
            logger.warning(
                f"[{PLUGIN_NAME}] 模型 {self.model} 不支持输出格式 {self.output_format}，"
                f"将自动改用 jpeg（支持：{'/'.join(sorted(self.caps_formats))}）。"
            )
        if self.count > 1 and not self.supports_group:
            logger.warning(
                f"[{PLUGIN_NAME}] 模型 {self.model} 不支持组图（一次多张），"
                f"count={self.count} 将按 1 张生成。"
            )
        if self.web_search and not self.supports_web_search:
            logger.warning(
                f"[{PLUGIN_NAME}] 配置开了 web_search，但模型 {self.model} 不支持联网搜索"
                "（文档明确目前只有 Seedream 5.0 lite 支持），该配置本次不生效。"
            )
        if self.optimize_prompt_mode == "fast" and not self.supports_fast_prompt:
            logger.warning(
                f"[{PLUGIN_NAME}] 配置选了 fast 提示词优化，但模型 {self.model} 不支持"
                "（文档明确 5.0 lite / 4.5 尚未支持），将按 standard 模式生成。"
            )

        # ---------- 启动自检：模型可能「未开通 / 需专属端点」时提前说清 ----------
        if self.model in MODEL_UNUSABLE:
            extra = ""
            if self.model in AGENT_PLAN_MODELS and "/api/plan/" not in self.api_url:
                extra = (
                    "该模型属于 Agent Plan / CodePlan 模型族，若你持有 CodePlan，"
                    "把 api_url 换成 Agent Plan 专属端点并配套专属 Key 也可以直接用。"
                )
            logger.warning(
                f"[{PLUGIN_NAME}] ⚠️ 模型 {self.model} 在本账号下无法路由到可用接入点"
                f"（能过模型校验，真发请求必被上游网关拒绝）。{ACTIVATION_HINT}。{extra}"
                + (
                    f"在此之前，插件会自动改用 {self.fallback_model} 出图，不会失败。"
                    if self.auto_fallback and self.fallback_model != self.model
                    else "插件已关闭 auto_fallback，请求会直接报错。"
                )
            )

        # ---------- 启动自检：费用提醒 ----------
        self.price_per_image = unit_price(self.model, self.size)
        self.free_quota = free_quota_of(self.model)
        if is_pro_model(self.model) and is_large_size_for_pro(self.size):
            logger.warning(
                f"[{PLUGIN_NAME}] 💰 费用提醒：{self.model} 在 {self.size}（>1.5K）下是 "
                f"{PRO_PRICE_TIERS['large']:.2f} 元/张，是同模型 1K/1.5K（"
                f"{PRO_PRICE_TIERS['small']:.2f} 元/张）的**两倍**。"
                "想省钱：把 size 改成 1K 或 1.5K，或换 "
                "doubao-seedream-5-0-flash-260915（0.12 元/张，全系最低且输入图免费）。"
            )
        elif is_pro_model(self.model):
            logger.warning(
                f"[{PLUGIN_NAME}] 💰 费用提醒：{self.model} **没有官方免费额度**（仅首张免费），"
                f"当前约 {self.price_per_image:.2f} 元/张。"
                "想白嫖免费额度可换 4.0（送 200 张）/ 4.5（送 200 张）/ 5.0-lite（送 50 张）；"
                "不要额度、只要最低单价则用 5.0 flash（0.12 元/张）。"
            )
        elif self.free_quota:
            logger.info(
                f"[{PLUGIN_NAME}] 💰 当前模型约 {self.price_per_image:.2f} 元/张；"
                f"官方免费额度 {self.free_quota} 张（用完会自动转付费，"
                "建议到控制台「模型算力计费」设用量上限）。"
            )

        # ---------- 运行时状态 ----------
        self._sem = asyncio.Semaphore(self.max_concurrent)
        self._processing: set[str] = set()
        self._last_ops: dict[str, float] = {}
        self._session: aiohttp.ClientSession | None = None
        self._cleanup_lock = asyncio.Lock()
        self._last_cleanup = 0.0
        # 省钱护栏的运行时数据
        self._daily: dict[str, int] = {}  # "YYYY-MM-DD|user_id" -> 今日已生成张数
        self._cache: dict[
            str, tuple[str, float]
        ] = {}  # 请求指纹 -> (本地图片路径, 时间戳)
        # 自愈记录：被实测证明「不收 output_format」的模型（运行时学习，重启即忘）
        self._no_format_models: set[str] = set()

        if not self.api_key:
            if self.backend == BACKEND_LOCAL_ONLY:
                logger.info(
                    f"[{PLUGIN_NAME}] 未配置 api_key —— 当前 backend=local_only，"
                    "只走本地 ComfyUI，不需要 Key。"
                )
            else:
                logger.error(
                    f"[{PLUGIN_NAME}] 未配置 api_key，云端生图不可用。"
                    "请在插件配置里填入火山方舟 API Key；"
                    "或把 backend 改成 local_only 只用本地 ComfyUI。"
                )
        if self.model in MODEL_UNUSABLE:
            logger.warning(
                f"[{PLUGIN_NAME}] ⚠️ 模型 {self.model} 实测在本账号下**无法生成**"
                f"（上游网关内部错误，与 Key / 余额 / 参数都无关）。"
                f"强烈建议把配置 model 改成 {MODEL_UNUSABLE[self.model]}。"
            )
        _checks = []
        if self.moderation_check_prompt:
            _checks.append("提示词")
        if self.moderation_check_image:
            _checks.append("图片")
        logger.info(
            f"[{PLUGIN_NAME}] v{PLUGIN_VERSION} 初始化完成 | "
            f"后端={BACKEND_ZH.get(self.backend, self.backend)} | "
            f"本地={self.comfy_url}（{self.comfy_width}x{self.comfy_height}, "
            f"steps={self.comfy_steps}, 超时{self.comfy_timeout:.0f}s） | "
            f"审查={'开(' + '+'.join(_checks) + ')' if self.moderation_enabled else '关'}"
            f"模型={self.moderation_provider_id or '会话默认'} | "
            f"人格参考图={'开' if self.persona_reference_enabled else '关'}"
            f"（{self.reference_dir}） | "
            f"云端模型={self.model} 尺寸={self.size} 格式={self.output_format} | "
            f"组图={'支持' if self.supports_group else '不支持'} | "
            f"参考图={'允许' if self.allow_reference else '禁用'}"
            f"({self.reference_transport}, 最多{self.max_reference_images}张) | "
            f"约 {self.price_per_image:.2f} 元/张 | "
            f"日限额={self.daily_limit if self.daily_limit else '不限'}（仅云端） | "
            f"缓存={str(self.cache_seconds) + 's' if self.cache_seconds else '关'} | "
            f"LLM工具={LLM_TOOL_NAME}"
        )

        try:
            self.context.activate_llm_tool(LLM_TOOL_NAME)
        except Exception as e:  # pragma: no cover - 防御性
            logger.warning(f"[{PLUGIN_NAME}] 激活 LLM 工具失败（可忽略）：{e}")

    # ==================================================================
    # 生命周期 / HTTP 会话
    # ==================================================================
    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=SESSION_TIMEOUT)
        return self._session

    async def terminate(self):
        """插件卸载：关闭会话 + 清理临时图片。"""
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        try:
            save_dir = StarTools.get_data_dir(PLUGIN_NAME) / "images"
            if save_dir.exists():
                for f in os.listdir(save_dir):
                    try:
                        os.remove(save_dir / f)
                    except OSError:
                        pass
        except Exception:
            pass
        logger.info(f"[{PLUGIN_NAME}] 已卸载，资源清理完成。")

    # ==================================================================
    # 临时图片清理
    # ==================================================================
    async def _maybe_cleanup(self):
        now = time.time()
        if now - self._last_cleanup < TEMP_RETENTION:
            return
        async with self._cleanup_lock:
            if time.time() - self._last_cleanup < TEMP_RETENTION:
                return
            self._last_cleanup = now
            try:
                save_dir = StarTools.get_data_dir(PLUGIN_NAME) / "images"
                if not save_dir.exists():
                    return
                for f in os.listdir(save_dir):
                    p = save_dir / f
                    try:
                        if p.is_file() and now - p.stat().st_mtime > TEMP_RETENTION:
                            os.remove(p)
                    except OSError:
                        pass
            except Exception as e:
                logger.warning(f"[{PLUGIN_NAME}] 临时图片清理异常：{e}")

    # ==================================================================
    # 参考图收集
    # ==================================================================
    @staticmethod
    def _image_ref_of(comp: Any) -> str | None:
        """从 Image 组件里取一个可用的引用（优先 url，其次 file / path）。"""
        for attr in ("url", "file", "path"):
            v = getattr(comp, attr, None)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    @staticmethod
    def _push_ref(bucket: list[str], ref: str | None):
        if ref and ref not in bucket:
            bucket.append(ref)

    def _collect_raw_refs(self, event: AstrMessageEvent) -> list[str]:
        """收集原始参考图引用：当前消息附图 + 引用消息附图 + 提示词里的链接。"""
        raw: list[str] = []

        for comp in event.get_messages() or []:
            if isinstance(comp, Reply):
                # 引用消息里的图片
                for inner in getattr(comp, "chain", None) or []:
                    if isinstance(inner, Image):
                        self._push_ref(raw, self._image_ref_of(inner))
            elif isinstance(comp, Image):
                self._push_ref(raw, self._image_ref_of(comp))

        # 提示词里直接写的图片链接
        text = self._own_text(event)
        for url in extract_image_urls_from_text(text):
            self._push_ref(raw, url)

        return raw

    def _own_text(self, event: AstrMessageEvent) -> str:
        """只取「用户自己说的」纯文本（不含引用消息的内容）。"""
        parts: list[str] = []
        for comp in event.get_messages() or []:
            if isinstance(comp, Plain) and getattr(comp, "text", None):
                parts.append(comp.text)
        return "".join(parts)

    async def _collect_reference_images(self, event: AstrMessageEvent) -> list[str]:
        """收集并归一化参考图，返回可直接喂给接口的引用列表。"""
        raw = self._collect_raw_refs(event)
        if not raw:
            return []

        picked = raw[: self.max_reference_images]
        if len(raw) > len(picked):
            logger.info(
                f"[{PLUGIN_NAME}] 参考图 {len(raw)} 张，超出上限只取前 {len(picked)} 张。"
            )

        out: list[str] = []
        for ref in picked:
            try:
                normalized = await self._normalize_ref(ref)
            except Exception as e:
                logger.warning(
                    f"[{PLUGIN_NAME}] 参考图处理失败 {sanitize_ref_for_log(ref)}：{e}"
                )
                normalized = None
            if normalized:
                out.append(normalized)
            else:
                logger.warning(
                    f"[{PLUGIN_NAME}] 参考图已跳过（无法识别为图片或过大）："
                    f"{sanitize_ref_for_log(ref)}"
                )
        return out

    async def _normalize_ref(self, ref: str, force_inline: bool = False) -> str | None:
        """把任意来源的参考图统一成方舟接受的形态。

        - data:image/... 与 base64:// → 直接整理成 data URI；
        - http(s) → 视 reference_transport 决定直链还是下载转 base64；
        - file:// / 本地路径 → 读文件转 base64。
        识别不出是图片（魔数不匹配）时返回 None。
        """
        ref = (ref or "").strip().strip("\"'")
        if not ref:
            return None

        low = ref.lower()
        if low.startswith("data:image/"):
            return ref
        if low.startswith("base64://"):
            return "data:image/png;base64," + ref[len("base64://") :]

        if low.startswith("http://") or low.startswith("https://"):
            if self.reference_transport == "url" and not force_inline:
                return ref
            return await self._url_to_data_uri(ref)

        local = local_path_from_ref(ref)
        if local:
            return self._file_to_data_uri(local)

        if looks_like_base64(ref):
            return "data:image/png;base64," + ref

        return None

    def _size_limit_bytes(self) -> int:
        return int(min(self.max_reference_mb * 1024 * 1024, REF_HARD_BYTES))

    def _file_to_data_uri(self, path: str) -> str | None:
        try:
            size = os.path.getsize(path)
        except OSError as e:
            logger.warning(f"[{PLUGIN_NAME}] 读取参考图失败：{e}")
            return None
        limit = self._size_limit_bytes()
        if size <= 0:
            return None
        if size > limit:
            logger.warning(
                f"[{PLUGIN_NAME}] 参考图过大（{readable_size(size)} > "
                f"{readable_size(limit)}），已跳过。"
            )
            return None
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            logger.warning(f"[{PLUGIN_NAME}] 读取参考图失败：{e}")
            return None
        return bytes_to_data_uri(data)

    async def _url_to_data_uri(self, url: str) -> str | None:
        limit = self._size_limit_bytes()
        try:
            async with self.session.get(
                url, headers=DOWNLOAD_HEADERS, allow_redirects=True
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[{PLUGIN_NAME}] 参考图下载失败 HTTP {resp.status}："
                        f"{sanitize_ref_for_log(url)}"
                    )
                    return None
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > limit:
                    logger.warning(
                        f"[{PLUGIN_NAME}] 参考图过大（{readable_size(int(cl))}），已跳过。"
                    )
                    return None
                data = await resp.read()
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] 参考图下载异常：{e}")
            return None

        if len(data) > limit:
            logger.warning(
                f"[{PLUGIN_NAME}] 参考图过大（{readable_size(len(data))}），已跳过。"
            )
            return None
        return bytes_to_data_uri(data)

    # ==================================================================
    # 请求体
    # ==================================================================
    def _compose_prompt(self, prompt: str) -> str:
        pieces = [
            p for p in (self.prompt_prefix, prompt.strip(), self.prompt_suffix) if p
        ]
        return truncate_prompt(" ".join(pieces))

    def _caps_of(self, model: str | None = None) -> dict[str, Any]:
        """取某个模型的能力表（省略时取当前配置模型的）。"""
        model = model or self.model
        return self.caps if model == self.model else resolve_model_caps(model)

    def _effective_size(
        self, size: str | None, model: str | None = None
    ) -> tuple[str, str]:
        """返回 (最终尺寸, 说明)。按模型能力自动降级。"""
        model = model or self.model
        caps = self._caps_of(model)
        caps_sizes = caps.get("sizes")
        want = (str(size).strip() if size else self.size) or self.size

        # 自定义宽高（方式 2）：先过「总像素区间 + 宽高比」校验，不合规就退回安全档位。
        # 不校验的话，用户写个 512x512 会被上游直接 400 打回，
        # 而错误文案只说 "parameter not valid"，很难自己看出到底为什么。
        if CUSTOM_SIZE_RE.match(want):
            why = check_custom_size(want, caps)
            if why:
                return SAFE_SIZE, f"（{want} 不可用：{why}；已改用 {SAFE_SIZE}）"
            return want, ""

        if caps_sizes is None:
            return want, ""
        supported = {s.upper() for s in caps_sizes}
        if want.upper() in supported:
            return want, ""
        return SAFE_SIZE, f"（模型 {model} 不支持 {want}，已改用 {SAFE_SIZE}）"

    def _effective_format(self, model: str | None = None) -> tuple[str, str]:
        model = model or self.model
        caps_formats = self._caps_of(model).get("formats")
        fmt = self.output_format if self.output_format in ("jpeg", "png") else "jpeg"
        if caps_formats is not None and fmt not in caps_formats:
            return "jpeg", f"（模型 {model} 仅支持 jpeg，已改用 jpeg）"
        return fmt, ""

    def _build_payload(
        self,
        prompt: str,
        refs: list[str],
        size: str | None = None,
        count: int | None = None,
        model: str | None = None,
    ) -> tuple[dict, int, str]:
        """返回 (请求体, 实际生成数量, 给用户的一句说明)。

        `model` 省略时用配置里的模型；传入别的模型用于 auto_fallback 重建请求
        （换模型会连带换能力表，尺寸 / 格式 / 组图都要按新模型重新判定）。

        重要：`sequential_image_generation` 只在「确实要一次多张」且模型支持组图时
        才会被加进请求体。5.0 pro 连 "disabled" 都不接受，单张请求带上它会直接 400。
        """
        model = model or self.model
        supports_group = bool(self._caps_of(model).get("seq"))
        notes: list[str] = []

        final_size, size_note = self._effective_size(size, model)
        if size_note:
            notes.append(size_note)
        final_fmt, fmt_note = self._effective_format(model)
        if fmt_note:
            notes.append(fmt_note)

        payload: dict = {
            "model": model,
            "prompt": prompt,
            "size": final_size,
            "response_format": "url",
            "watermark": self.watermark,
        }
        # 联网搜索：只有「模型支持（目前仅 5.0 lite）」且「用户开了」才下发。
        # 不支持时静默忽略并明确告知，而不是让请求带着一个必被拒的参数去撞一次 400。
        if self.web_search:
            if self._caps_of(model).get("web_search"):
                payload["tools"] = [{"type": "web_search"}]
            else:
                notes.append(f"（模型 {model} 不支持联网搜索，已按普通生成）")
        # 提示词优化：standard 是服务端默认值，不必下发；只有 fast 才需要显式声明。
        if self.optimize_prompt_mode == "fast":
            if self._caps_of(model).get("fast_prompt"):
                payload["optimize_prompt_options"] = {"mode": "fast"}
            else:
                notes.append(f"（模型 {model} 不支持 fast 提示词优化，已按 standard）")
        # output_format 只在「确实需要」时才发：
        #   · jpeg 是接口缺省值，不传等价；
        #   · 4.0 / 4.5 等模型**压根不收**这个参数，传了直接 400 InvalidParameter；
        #   · 只有明确要 png 且模型支持时才有必要传。
        # 另外带自愈记录：某模型一旦被实测拒收过，之后一律不带。
        want_fmt = final_fmt if final_fmt in ("jpeg", "png") else "jpeg"
        if want_fmt == "png":
            if model in self._no_format_models:
                notes.append(f"（模型 {model} 不接受 output_format，已按默认格式输出）")
            else:
                payload["output_format"] = "png"
        if self.seed >= 0:
            payload["seed"] = self.seed

        # 参考图：单张按文档传字符串，多张传数组。
        # 张数要先按模型上限裁剪 —— 5.0 pro 只能收 10 张，lite / 4.5 / 4.0 能收 14 张，
        # 超传上游会直接整单报错，所以这里先截断并明确告知，而不是让整个请求失败。
        if refs:
            cap = int(self._caps_of(model).get("max_refs") or REF_HARD_LIMIT)
            if len(refs) > cap:
                notes.append(
                    f"（模型 {model} 最多接收 {cap} 张参考图，"
                    f"多出的 {len(refs) - cap} 张已忽略）"
                )
                refs = refs[:cap]
            payload["image"] = refs[0] if len(refs) == 1 else list(refs)

        want = self.count if count is None else max(1, int(count))
        if want > 1:
            if not supports_group:
                notes.append(f"（模型 {model} 不支持组图，已按 1 张生成）")
                want = 1
            else:
                room = TOTAL_IMAGE_LIMIT - len(refs)
                capped = max(1, min(want, room))
                if capped != want:
                    notes.append(
                        f"（参考图占 {len(refs)} 张，生成数量已调整为 {capped} 张）"
                    )
                want = capped
                if want > 1:
                    payload["sequential_image_generation"] = "auto"
                    payload["sequential_image_generation_options"] = {
                        "max_images": want
                    }

        return payload, want, "".join(notes)

    def _payload_for_log(self, payload: dict) -> str:
        """把请求体转成可安全写日志的形式（参考图只留摘要，绝不输出 base64）。"""
        safe: dict = {}
        for k, v in payload.items():
            if k == "image":
                items = v if isinstance(v, list) else [v]
                safe[k] = (
                    f"<{len(items)} 张: "
                    + ", ".join(sanitize_ref_for_log(i) for i in items)
                    + ">"
                )
            elif k == "prompt":
                p = str(v)
                safe[k] = p if len(p) <= 80 else p[:80] + f"...(共{len(p)}字)"
            else:
                safe[k] = v
        return json.dumps(safe, ensure_ascii=False)

    # ==================================================================
    # 调用接口
    # ==================================================================
    async def _post_once(
        self, payload: dict, url: str | None = None
    ) -> tuple[int, str]:
        """发一次请求。url 省略时用配置的接入点（默认标准端点）。"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with self.session.post(
            url or self.api_url, headers=headers, json=payload
        ) as resp:
            return resp.status, await resp.text()

    @staticmethod
    def _parse_error(body: str) -> tuple[str, str]:
        """从上游响应里取出 (code, message)。"""
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return "", str(body or "")[:300]
        err = data.get("error") if isinstance(data, dict) else None
        if not isinstance(err, dict):
            err = data if isinstance(data, dict) else {}
        code = str(err.get("code") or data.get("code") or "").strip()
        msg = str(err.get("message") or data.get("message") or "").strip()
        return code, msg

    @staticmethod
    def _looks_like_routing_error(code: str, msg: str) -> bool:
        """识别上游「模型能校验、不能生成」的网关内部错误。"""
        if not code.startswith("InvalidParameter"):
            return False
        low = (msg or "").lower()
        return any(m in low for m in ROUTING_ERROR_MARKERS)

    # 「模型在本账号下不可用」的错误码前缀：
    #   ModelNotOpen            —— 模型没开通（2026-09-29 实测：flash 未开通时就是它）
    #   InvalidEndpointOrModel  —— 模型 ID 不存在 / 无权限，含 .NotFound 后缀
    # 它们和「网关路由失败」是同一类问题：剔参数重试毫无意义，应该交给 auto_fallback。
    MODEL_UNAVAILABLE_CODES = ("ModelNotOpen", "InvalidEndpointOrModel")

    @classmethod
    def _is_model_unavailable(cls, code: str, msg: str) -> bool:
        """模型级的不可用（未开通 / 无权限 / 路由不到接入点），与参数无关。"""
        if cls._looks_like_routing_error(code, msg):
            return True
        return any(code.startswith(c) for c in cls.MODEL_UNAVAILABLE_CODES)

    @classmethod
    def _explain_error(cls, status: int, body: str, model: str = "") -> str:
        """把上游报错转成「人话 + 原始 code/message」，方便定位。

        判定顺序：欠费 → 额度 → 模型不可用 → 错误码表 → HTTP 状态码兜底。
        欠费必须排最前：它在方舟上是 403 + type=Forbidden + code=AccountOverdueError，
        和「API 没权限」长得一模一样，只有上游原文才能定性（v1.0.2 踩过的坑）。
        """
        code, msg = cls._parse_error(body)

        if looks_like_arrears(code, msg, status):
            detail = (
                "💳 账户已欠费（余额不足）——这不是 API Key 或模型配置的问题。"
                "请到火山方舟控制台充值后重试"
            )
        elif looks_like_quota(code, msg):
            detail = "📉 账户额度已用尽（含免费额度），请到火山方舟控制台充值后重试"
        elif cls._looks_like_routing_error(code, msg):
            # 模型能过存在性校验，但网关解析不到可用接入点（典型：lite 系列）。
            # 这种报错长得像"参数不合法"，其实和参数、Key、余额都无关，
            # 必须同时说清「根因」与「两条出路」，否则用户只能瞎试。
            cur = (model or "").strip() or "当前模型"
            alt = MODEL_UNUSABLE.get((model or "").strip(), DEFAULT_MODEL)
            paths = [ACTIVATION_HINT]
            if cur in AGENT_PLAN_MODELS:
                paths.append(
                    "若你持有 CodePlan / Agent Plan，改用它的专属端点与专属 Key 也能直接用"
                )
            paths.append(f"或把插件配置的 model / fallback_model 换成 {alt}")
            detail = (
                f"🚧 模型 `{cur}` 在本账号下无法路由到可用接入点"
                f"（上游网关内部错误，与你的 Key / 余额 / 参数都无关）。"
                + "；".join(paths)
            )
        elif code.startswith("InvalidParameter") and cls._named_param(msg) == "model":
            # 上游有时把「模型不对」也归到 InvalidParameter，
            # 只说「参数不合法」等于没说，这里直接点明是模型的问题。
            detail = "模型不可用：模型 ID 不存在或未在本账号开通，请检查 model 配置"
        else:
            detail = ERROR_MAP.get(code)
            if not detail:
                for key, value in ERROR_MAP.items():
                    if code.startswith(key):
                        detail = value
                        break
        if not detail:
            if status == 401:
                detail = "API Key 无效或已失效"
            elif status == 403:
                detail = (
                    "请求被拒绝（HTTP 403）。常见原因：账户欠费，或该模型未在本账号开通。"
                    "发送 /画图诊断 可精确定位"
                )
            elif status == 404:
                detail = "模型或接口不存在（请检查 model 配置是否正确、是否已开通）"
            elif status == 429:
                detail = "调用频率超限，请稍后再试"
            elif status >= 500:
                detail = "上游服务异常，请稍后重试"

        head = f"{detail}（HTTP {status}，code={code or '未知'}）"
        tail = (msg or "").strip()
        return f"{head}\n上游原文：{tail[:200]}" if tail else head

    @staticmethod
    def _named_param(message: str) -> str | None:
        """上游报错里用反引号点名的参数名，例如 `sequential_image_generation`。"""
        m = re.search(r"`([A-Za-z_][A-Za-z0-9_]*)`", message or "")
        return m.group(1) if m else None

    def _repairable_param(
        self, body: str, payload: dict, dropped: set[str]
    ) -> str | None:
        """判断这次失败能否靠「剔除某个可选参数」自救。返回要剔除的参数名。"""
        code, msg = self._parse_error(body)
        if not code.startswith("InvalidParameter"):
            return None
        name = self._named_param(msg)
        if not name or name not in payload or name in dropped:
            return None
        if name not in OPTIONAL_PARAMS:
            return None
        return name

    async def _call_api(self, payload: dict) -> list[str]:
        """调用生图接口。

        三层容错：
          1) 网络类错误（超时/连接失败）有限重试；
          2) 上游因某个**可选参数**报 InvalidParameter 时，自动剔除该参数重试一次
             —— 这样即使方舟调整参数支持范围，插件也能自己活下来；
          3) 命中「模型在本账号下不可用」的错误（见 ROUTING_ERROR_MARKERS 与
             MODEL_UNAVAILABLE_CODES：路由失败 / 未开通 ModelNotOpen）时
             直接抛 ModelRoutingError，交给上层用 fallback_model 回退。
             这种错剔参数重试毫无意义，只会白跑几次往返。
        """
        if not self.api_key:
            raise RuntimeError("未配置火山方舟 API Key（插件配置 api_key）")

        payload = dict(payload)
        dropped: set[str] = set()
        net_attempt = 0
        net_attempts = self.retry_times + 1

        while True:
            try:
                status, body = await self._post_once(payload)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                net_attempt += 1
                if net_attempt < net_attempts:
                    await asyncio.sleep(1.5 * net_attempt)
                    continue
                raise RuntimeError(f"网络请求失败：{e}") from e

            if status != 200:
                used_model = str(payload.get("model") or self.model)
                code, msg = self._parse_error(body)
                if self._is_model_unavailable(code, msg):
                    # 模型未开通（ModelNotOpen），或网关解析不到可用接入点。
                    # 剔参数重试没有意义 —— 直接交给上层决定是否回退。
                    raise ModelRoutingError(
                        self._explain_error(status, body, used_model), model=used_model
                    )
                bad = self._repairable_param(body, payload, dropped)
                if (
                    not bad
                    and "output_format" in payload
                    and "output_format" not in dropped
                ):
                    # 上游报了 InvalidParameter 却**没点名**是哪个参数。
                    # 先怀疑最常见的不支持项 output_format，剔掉重试一次。
                    # 参数校验失败不计费，这一步不产生任何费用。
                    bad = "output_format"
                if bad and len(dropped) < MAX_PARAM_REPAIRS:
                    for key in [
                        k for k in payload if k == bad or k.startswith(bad + ".")
                    ]:
                        payload.pop(key, None)
                    dropped.add(bad)
                    if bad == "output_format":
                        self._no_format_models.add(used_model)  # 记住：之后不再带
                    logger.warning(
                        f"[{PLUGIN_NAME}] 上游不接受参数 `{bad}`，已剔除并重试。"
                        f"（这不是你的配置问题，插件已自动规避）"
                    )
                    continue
                raise RuntimeError(self._explain_error(status, body, used_model))

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                raise RuntimeError(f"接口返回非 JSON：{body[:200]}")

            urls = [
                it.get("url")
                for it in (data.get("data") or [])
                if isinstance(it, dict) and it.get("url")
            ]
            if not urls:
                # 部分模型会返回 b64_json
                for it in data.get("data") or []:
                    if isinstance(it, dict) and it.get("b64_json"):
                        urls.append(f"base64://{it['b64_json']}")
                if not urls:
                    raise RuntimeError("接口未返回图片地址。")
            return urls

    # ==================================================================
    # 图片落盘 / 发送
    # ==================================================================
    async def _download_to_file(self, ref: str) -> str | None:
        """把 http 链接下载到本地；base64/data URI 也能落地。返回本地路径。"""
        asyncio.create_task(self._maybe_cleanup())

        raw: bytes | None = None
        if ref.startswith("base64://"):
            import base64 as _b64

            try:
                raw = _b64.b64decode(ref[len("base64://") :])
            except Exception:
                return None
        elif ref.startswith("data:image/"):
            import base64 as _b64

            try:
                raw = _b64.b64decode(ref.split(",", 1)[1])
            except Exception:
                return None
        else:
            try:
                async with self.session.get(
                    ref, headers=DOWNLOAD_HEADERS, allow_redirects=True
                ) as resp:
                    if resp.status != 200:
                        return None
                    raw = await resp.read()
            except Exception:
                return None

        if not raw:
            return None
        save_dir = StarTools.get_data_dir(PLUGIN_NAME) / "images"
        save_dir.mkdir(parents=True, exist_ok=True)
        # 扩展名按**真实字节**定，而不是按云端配置的 output_format：
        # v2.0.0 起本地 ComfyUI 的图也走这条落地路径，而它的出图格式由工作流的
        # 保存节点决定（实测自带工作流产出 PNG）。照 output_format 命名会把 png
        # 存成 .jpg —— 不只难看，审查环节把这个路径交给多模态模型时，
        # 扩展名与真实 MIME 不符会被部分视觉 API 直接拒收。
        ext = ""
        mime = sniff_image_mime(raw) or ""
        if "/" in mime:
            ext = mime.split("/", 1)[1].lower()
            if ext == "jpeg":
                ext = "jpg"
        if ext not in ("png", "jpg", "webp", "gif", "bmp", "avif"):
            ext = "png" if self.output_format == "png" else "jpg"
        path = save_dir / f"doubao_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"
        try:
            with open(path, "wb") as f:
                f.write(raw)
        except OSError as e:
            logger.warning(f"[{PLUGIN_NAME}] 保存图片失败：{e}")
            return None
        return str(path)

    async def _materialize(self, url: str) -> tuple[Image, str | None]:
        """把上游返回的图片变成可发送组件，并**同时返回真实本地路径**。

        为什么要一起返回路径：Image.fromFileSystem() 会把 `file` 设成
        `file:///C:/...` 这样的 URI、把真实路径放在 `path` 里。
        同图缓存需要的是一个能直接喂给 Image.fromFileSystem 的本地路径，
        所以在这里显式带出来，不去猜组件的字段。
        """
        if self.download_locally:
            local = await self._download_to_file(url)
            if local:
                return Image.fromFileSystem(local), local
            logger.warning(f"[{PLUGIN_NAME}] 本地落地失败，回退直发 URL。")
        if url.startswith("http://") or url.startswith("https://"):
            return Image.fromURL(url), None
        # base64 兜底
        if url.startswith("base64://"):
            return Image.fromBase64(url[len("base64://") :]), None
        if url.startswith("data:image/"):
            return Image.fromBase64(url.split(",", 1)[1]), None
        raise RuntimeError("无法识别的图片地址")

    async def _to_image_component(self, url: str) -> Image:
        comp, _ = await self._materialize(url)
        return comp

    # ==================================================================
    # 人格参考图（v2.0.0）
    #   参考图放插件目录的 refs/ 下，文件名 = AI 人格名。
    # ==================================================================
    def _refs_dir(self) -> str:
        """参考图目录。默认插件目录下的 refs/，可由 reference_dir 覆盖。"""
        return self.reference_dir

    async def _refs_report(self, event: AstrMessageEvent) -> str:
        """`/画图参考` 的正文：目录现状 + 每个人格能不能对上图。"""
        names: list[str] = []
        try:
            pm = getattr(self.context, "persona_manager", None)
            if pm is not None:
                for p in await pm.get_all_personas() or []:
                    if isinstance(p, dict):
                        n = p.get("persona_id") or p.get("name") or ""
                    else:
                        n = getattr(p, "persona_id", "") or ""
                    n = str(n).strip()
                    if n and n not in names:
                        names.append(n)
        except Exception as e:
            logger.debug(f"[{PLUGIN_NAME}] 列举人格失败（忽略）：{e}")

        text = describe_refs(self._refs_dir(), names)

        cur, origin = await self._current_persona_name(event)
        text += "\n\n当前会话人格："
        text += (
            f"{cur}（来源：{origin}）"
            if cur
            else "取不到（将用配置 persona_name_default 兜底）"
        )
        text += "\n" + await self._persona_debug(event)

        if self.persona_reference_enabled:
            if cur:
                _hit, _how = resolve_ref(
                    self._refs_dir(), cur, self.reference_aliases, self._ref_table()
                )
                if _hit:
                    text += f"\n✅ 已匹配到参考图：{os.path.basename(_hit)}（{_how}）"
                elif list_ref_files(self._refs_dir()):
                    text += (
                        f"\n❌ 人格「{cur}」在目录里没有对应文件"
                        " —— 这就是「怎么画都只出文生图」的原因。"
                    )
                    _hint = suggest_text(self._refs_dir(), cur)
                    if _hint:
                        text += "\n" + _hint
                else:
                    text += "\n❌ 目录里一张图都没有，先把参考图放进来。"
        if not self.persona_reference_enabled:
            text += "\n⚠️ persona_reference_enabled 已关，插件不会自动取人格参考图。"
        if self.reference_aliases:
            pairs = "、".join(f"{k}→{v}" for k, v in self.reference_aliases.items())
            text += f"\n已配置别名：{pairs}"
        return text

    def _ref_table(self) -> dict[str, str]:
        """带 mtime 缓存的参考图索引（每次搜索都扫目录太浪费）。"""
        path = self._refs_dir()
        try:
            mtime = os.path.getmtime(path) if os.path.isdir(path) else -1.0
        except OSError:
            mtime = -1.0
        cached_at, table = self._ref_cache
        if mtime >= 0 and mtime == cached_at:
            return table
        table = scan_ref_dir(path)
        self._ref_cache = (mtime, table)
        return table

    async def _current_persona_name(self, event: AstrMessageEvent) -> tuple[str, str]:
        """取当前生效的人格名。返回 (名字, 来源说明)；取不到返回 ("", "")。

        优先级：
          1. 官方口径 persona_manager.resolve_selected_persona —— AstrBot 自己决定
             system prompt 用谁时走的就是它（含 umo 级会话规则、会话人格、默认人格）。
             插件必须跟它对表，否则会出现「AI 明明是某人，插件却按另一个名字找参考图」；
          2. 会话表里的 persona_id；
          3. persona_manager 的默认人格；
          4. 配置 persona_name_default。
        """
        umo = event.unified_msg_origin

        # ---- 1) 官方口径 ----
        conv_pid = None
        try:
            cm = getattr(self.context, "conversation_manager", None)
            if cm is not None:
                cid = await cm.get_curr_conversation_id(umo)
                if cid:
                    conv = await cm.get_conversation(umo, cid)
                    conv_pid = getattr(conv, "persona_id", None) if conv else None
        except Exception as e:
            logger.debug(f"[{PLUGIN_NAME}] 读会话人格失败（忽略）：{e}")

        try:
            pm = getattr(self.context, "persona_manager", None)
            resolver = getattr(pm, "resolve_selected_persona", None)
            if callable(resolver):
                try:
                    platform_name = str(event.get_platform_name() or "")
                except Exception:
                    platform_name = ""
                picked = await resolver(
                    umo=umo,
                    conversation_persona_id=conv_pid,
                    platform_name=platform_name,
                )
                if picked:
                    pid = picked[0]
                    persona_obj = picked[1]
                    if isinstance(persona_obj, dict):
                        name = str(persona_obj.get("name") or "")
                    elif persona_obj is not None:
                        name = str(getattr(persona_obj, "name", "") or "")
                    else:
                        name = ""
                    name = name or str(pid or "")
                    if name and name not in _PERSONA_IGNORE:
                        return name, "会话生效人格"
        except Exception as e:
            logger.debug(f"[{PLUGIN_NAME}] 官方人格解析失败（转用兜底）：{e}")

        # 官方解析没给出可用人格时，退回会话表里记着的 persona_id
        if conv_pid and str(conv_pid) not in _PERSONA_IGNORE:
            return str(conv_pid), "会话人格"
        # 2) 兜底：persona_manager 的默认人格（agent_runner 里配的那个）
        try:
            pm = getattr(self.context, "persona_manager", None)
            if pm is not None:
                persona = await pm.get_default_persona_v3(umo)
                if isinstance(persona, dict):
                    name = str(persona.get("name") or "")
                else:
                    name = str(getattr(persona, "name", "") or "")
                if name and name.lower() not in ("default", "默认"):
                    return name, "默认人格"
        except Exception as e:
            logger.debug(f"[{PLUGIN_NAME}] 读默认人格失败（忽略）：{e}")
        # 3) 配置兜底
        if self.persona_name_default:
            return self.persona_name_default, "配置指定"
        return "", ""

    async def _persona_debug(self, event: AstrMessageEvent) -> str:
        """一行诊断：插件到底把谁当成了「当前人格」。

        参考图是按人格名找文件的，所以「对不上」几乎总是这一行的问题。
        把每一步的原始输入都摆出来，比让用户猜快得多。
        """
        parts: list[str] = []
        umo = event.unified_msg_origin

        conv_pid = ""
        try:
            cm = getattr(self.context, "conversation_manager", None)
            if cm is not None:
                cid = await cm.get_curr_conversation_id(umo)
                if cid:
                    conv = await cm.get_conversation(umo, cid)
                    conv_pid = str(getattr(conv, "persona_id", None) or "")
        except Exception as e:
            conv_pid = f"<读取失败 {e}>"
        parts.append(f"会话 persona_id={conv_pid or '（空）'}")

        try:
            parts.append(f"平台={event.get_platform_name() or '（未知）'}")
        except Exception:
            parts.append("平台=（未知）")

        dp = ""
        try:
            pm = getattr(self.context, "persona_manager", None)
            if pm is not None:
                persona = await pm.get_default_persona_v3(umo)
                if isinstance(persona, dict):
                    dp = str(persona.get("name") or "")
                else:
                    dp = str(getattr(persona, "name", "") or "")
        except Exception as e:
            dp = f"<读取失败 {e}>"
        parts.append(f"默认人格={dp or '（空）'}")

        cur, origin = await self._current_persona_name(event)
        parts.append(f"⟹ 实际按「{cur or '（无）'}」找参考图（{origin or '无来源'}）")
        if self.persona_name_default:
            parts.append(f"配置兜底={self.persona_name_default}")
        return "｜".join(parts)

    async def _persona_ref_for(
        self, event: AstrMessageEvent
    ) -> tuple[str | None, str, str]:
        """按当前人格找参考图。返回 (路径 or None, 人格名, 命中说明)。"""
        if not self.persona_reference_enabled:
            return None, "", ""
        name, origin = await self._current_persona_name(event)
        if not name:
            return None, "", ""
        path, how = resolve_ref(
            self._refs_dir(), name, self.reference_aliases, self._ref_table()
        )
        if not path:
            # 把 how 一起带出去 —— 调用方要靠它说清「为什么没找到」
            return None, name, how
        return path, name, f"{origin}「{name}」→ {how}"

    # ==================================================================
    # 提示词层（v2.0.3）：system 注入 + 双后端槽位
    # ==================================================================
    def _adapt_ref_slots(
        self,
        text: str,
        *,
        local_ref_count: int,
        cloud_ref_count: int,
    ) -> tuple[str, str, str, str]:
        """同一条提示词适配到两个后端。

        返回 (本地版, 云端版, 给 LLM 的提示, 给用户的提示)。

        LLM 只写 ``<imageN>``：本地 Qwen 原样保留，云端 Seedream 翻成自然语言。
        序号超出**本次实际**参考图张数的算悬空引用，按句删掉 —— 宁可少一句，
        也不要让模型照着一张开不存在的图去画。
        """
        local = adapt_slots(text, backend=SLOT_BACKEND_QWEN, ref_count=local_ref_count)
        cloud = adapt_slots(
            text, backend=SLOT_BACKEND_SEEDREAM, ref_count=cloud_ref_count
        )
        if local.changed or cloud.changed:
            logger.info(
                f"[{PLUGIN_NAME}] 提示词槽位适配：本地 {local.describe()}；"
                f"云端 {cloud.describe()}"
            )
        # 两边丢的句子可能不同（本地只喂 1 张，云端可能喂多张），报告更严重的那个
        worse = local if len(local.dropped) >= len(cloud.dropped) else cloud
        return local.text, cloud.text, worse.note_llm(), worse.note_zh()

    async def _prompt_guide_block(self, event: AstrMessageEvent) -> str:
        """拼注入 system_prompt 的那一段（含当前人格与参考图现状）。

        模型看不到「当前人格是谁、有没有参考图」这两个事实，就只能靠猜；
        猜错选了 none，画出来的人自然不像。把事实喂给它，决策代价就降下来了。
        """
        umo = event.unified_msg_origin
        now = time.time()
        hit = self._guide_cache.get(umo)
        if hit and now - hit[0] <= 30.0:
            return hit[1]

        name = ""
        try:
            name, _origin = await self._current_persona_name(event)
        except Exception as e:
            logger.debug(f"[{PLUGIN_NAME}] 注入提示词时解析人格失败（忽略）：{e}")

        fname = ""
        suggestion = ""
        candidates: list[str] = []
        try:
            path, _pname, _how = await self._persona_ref_for(event)
            if path:
                fname = os.path.basename(path)
            else:
                files = list_ref_files(self._refs_dir())
                candidates = [os.path.basename(p) for _s, p in files]
                if name and candidates:
                    suggestion = suggest_text(self._refs_dir(), name)
        except Exception as e:
            logger.debug(f"[{PLUGIN_NAME}] 注入提示词时解析参考图失败（忽略）：{e}")

        # 工作流清单：只有本轮真有可能走本地时才写进去 —— 纯云端用户看到
        # 一堆「美感版/精准版」只会白占注意力，那几套工作流对他们毫无意义。
        local_ok = self.backend in (BACKEND_LOCAL_FIRST, BACKEND_LOCAL_ONLY)
        menu = ""
        if local_ok and self.comfy_workflow_select:
            try:
                menu = self._workflow_menu_text()
            except Exception as e:
                logger.debug(f"[{PLUGIN_NAME}] 注入提示词时扫描工作流失败（忽略）：{e}")

        block = build_system_block(
            persona_name=name,
            ref_filename=fname,
            candidates=candidates,
            suggestion=suggestion,
            workflow_menu=menu,
            local_available=local_ok,
        )
        self._guide_cache[umo] = (now, block)
        if len(self._guide_cache) > 200:  # 简单限长，别随会话数无限涨
            oldest = sorted(self._guide_cache, key=lambda k: self._guide_cache[k][0])
            for k in oldest[:100]:
                self._guide_cache.pop(k, None)
        return block

    def _tool_enabled(self, req: Any) -> bool:
        """本轮请求里本插件的生图工具是否可用。拿不到信息时按可用处理。"""
        ts = getattr(req, "func_tool", None)
        if ts is None:
            return True
        tools = getattr(ts, "tools", None)
        if tools is None:
            return True
        names = [getattr(t, "name", "") for t in tools]
        if not names:
            return False
        return LLM_TOOL_NAME in names

    @filter.on_llm_request(priority=100)
    async def inject_prompt_guide(self, event: AstrMessageEvent, req: Any):
        """把「生图前自查」注入 system_prompt。

        为什么必须注入而不只写在工具说明里：见 ``_prompt_guide_block``。

        为什么拆两处：完整指令（固定模板 + 变量规则 + few-shot）一千多字，
        全塞 system_prompt 会挤掉人格提示词的注意力。这里只放决策规则与动态
        事实（短而硬），完整模板留在工具说明里 —— 模型要调用工具时必然看到。
        """
        if not self.prompt_guide_enabled:
            return
        try:
            if not self._tool_enabled(req):
                return
            block = await self._prompt_guide_block(event)
            if not block:
                return
            before = getattr(req, "system_prompt", "") or ""
            after = inject_into_system_prompt(before, block)
            if after != before:
                req.system_prompt = after
                logger.info(
                    f"[{PLUGIN_NAME}] 已向 system_prompt 注入「生图前自查」"
                    f"（{len(block)} 字符）"
                )
        except Exception as e:
            # 注入失败绝不能影响正常对话
            logger.warning(f"[{PLUGIN_NAME}] 注入生图提示词失败（已忽略）：{e}")

    # ==================================================================
    # 本地 ComfyUI（v2.0.0）
    # ==================================================================
    def _comfy_base(self) -> str:
        return normalize_comfy_url(self.comfy_url)

    # ---- 可选工作流清单：让模型自己挑（v3.1.0）----

    def _local_workflows(self) -> list[WorkflowEntry]:
        """扫一遍预设目录。目录 mtime 没变就复用上次结果（生图是热路径）。"""
        path = self.comfy_workflow_dir
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            self._wf_cache = (-1.0, [])
            return []
        if self._wf_cache[0] == mtime:
            return self._wf_cache[1]
        entries = scan_workflow_dir(path)
        self._wf_cache = (mtime, entries)
        return entries

    def _entry_info(
        self, entry: WorkflowEntry
    ) -> tuple[dict | None, WorkflowSpec | None, str]:
        """加载某个预设 → (工作流, 语义映射, 错误说明)。按路径缓存。"""
        cached = self._entry_cache.get(entry.path)
        if cached is not None:
            return cached
        try:
            wf, spec = self._read_workflow_file(entry.path, f"工作流「{entry.name}」")
            out: tuple[dict | None, WorkflowSpec | None, str] = (wf, spec, "")
        except Exception as e:  # noqa: BLE001 —— 单个预设坏掉不该拖垮整份清单
            out = (None, None, str(e))
        self._entry_cache[entry.path] = out
        return out

    def _workflow_menu(self) -> list[tuple[WorkflowEntry, WorkflowSpec]]:
        """能用的预设：读得通、认得出采样器与提示词节点。坏掉的直接不进清单。"""
        out: list[tuple[WorkflowEntry, WorkflowSpec]] = []
        for entry in self._local_workflows():
            wf, spec, _err = self._entry_info(entry)
            if wf is not None and spec is not None:
                out.append((entry, spec))
        return out

    def _workflow_menu_text(self) -> str:
        """给模型看的清单。除了用途，还钉上「吃不吃参考图」这个硬事实 ——
        模型光看用途描述猜不出这一点，猜错就会白丢参考图。"""
        items = self._workflow_menu()
        lines: list[str] = []
        for entry, spec in items:
            tag = "支持参考图" if spec.supports_reference else "纯文生图"
            text = " ".join(entry.desc.split())
            if len(text) > 64:
                text = text[:63] + "…"
            head = f"· {entry.name}（{tag}）"
            lines.append(f"{head}：{text}" if text else head)
        return "\n".join(lines)

    def _pick_local_workflow(
        self, want: str, need_ref: bool
    ) -> tuple[WorkflowEntry | None, str]:
        """把「模型想要哪套 + 这次有没有参考图」落到一套具体预设上。

        返回 (预设, 给模型的说明)；预设为 None = 让调用方回落插件自带的那两个。

        规则按优先级：
          1. 模型点名了 → 用它。点名的与参考图需求冲突时**以「吃不吃参考图」为准改选**，
             并如实说清楚 —— 静默换一套跑出来的图，模型和用户都会莫名其妙。
          2. 没点名：需要参考图 → 挑第一套支持参考图的；否则先看配置指定的默认，
             再否则挑第一套纯文生图的。
        """
        if not self.comfy_workflow_select:
            return None, ""
        items = self._workflow_menu()
        if not items:
            return None, ""
        available = "、".join(e.name for e, _s in items)

        want = str(want or "").strip()
        if not want:
            return self._default_local_pick(items, need_ref), ""

        hit, _why = match_workflow([e for e, _s in items], want)
        if hit is None:
            # 点名不存在：照常出图（用默认挑法），但必须如实说清楚，
            # 免得模型以为「我点的那套生效了」而在下一轮继续点它。
            return self._default_local_pick(items, need_ref), (
                f"（你点名的「{want}」不在本机清单里，本次按默认挑了。"
                f"可选：{available}）"
            )
        found = next((p for p in items if p[0].key == hit.key), None)
        if found is None:  # pragma: no cover —— 清单同源，正常走不到
            return self._default_local_pick(items, need_ref), (
                f"（你点名的「{hit.name}」本机加载不了，本次按默认挑了。"
                f"可选：{available}）"
            )

        entry, note = self._reconcile_pick(found[0], found[1], items, need_ref)
        if entry is None:
            # 点名的与参考图需求互斥、又没有替代品 —— 仍然出图，退到默认
            return self._default_local_pick(items, need_ref), note
        return entry, note

    def _reconcile_pick(
        self,
        entry: WorkflowEntry,
        spec: WorkflowSpec,
        items: list[tuple[WorkflowEntry, WorkflowSpec]],
        need_ref: bool,
    ) -> tuple[WorkflowEntry | None, str]:
        """把「点名的那套」与「这次要不要参考图」对齐；冲突时改选并说明理由。

        为什么以参考图为准：参考图是用户/AI 明确给的身份约束，丢了它画出来的人
        就不像；而工作流只是画法偏好，换一套仍然能出图。两者冲突时牺牲后者。
        """
        if need_ref and not spec.supports_reference:
            alt = next((p for p in items if p[1].supports_reference), None)
            if alt is None:
                return None, f"（「{entry.name}」不吃参考图，且清单里没有支持参考图的预设）"
            return alt[0], (
                f"（本次要用参考图，而「{entry.name}」不吃参考图，"
                f"已改用「{alt[0].name}」）"
            )
        if not need_ref and spec.supports_reference:
            alt = next((p for p in items if not p[1].supports_reference), None)
            if alt is None:
                return None, f"（「{entry.name}」必须有参考图，但这次没有可用的参考图）"
            return alt[0], (
                f"（「{entry.name}」必须有参考图，但这次没有可用的参考图，"
                f"已改用「{alt[0].name}」）"
            )
        return entry, ""

    def _default_local_pick(
        self,
        items: list[tuple[WorkflowEntry, WorkflowSpec]],
        need_ref: bool,
    ) -> WorkflowEntry:
        """没点名（或点名失败）时挑哪套。

        顺序：要参考图就先找能吃参考图的 → 配置指定的默认 → 第一套纯文生图
        → 清单里第一套（全是参考图工作流时的兜底）。
        """
        if need_ref:
            alt = next((p for p in items if p[1].supports_reference), None)
            if alt is not None:
                return alt[0]
        if self.comfy_default_workflow:
            hit, _why = match_workflow(
                [e for e, _s in items], self.comfy_default_workflow
            )
            if hit is not None:
                found = next((p for p in items if p[0].key == hit.key), None)
                if found is not None and (not need_ref or found[1].supports_reference):
                    return found[0]
        alt = next((p for p in items if not p[1].supports_reference), None)
        if alt is not None:
            return alt[0]
        return items[0][0]

    def _workflow_file(self, kind: str) -> str:
        """兜底工作流的 JSON 路径。kind: t2i（纯文本）/ ref（参考图）。

        仅在「预设清单为空」或「清单里的都被关掉」时才走到这里 ——
        有预设时优先用预设（那一套才是模型自己选的）。
        """
        custom = self.comfy_ref_workflow if kind == "ref" else self.comfy_t2i_workflow
        if custom:
            return (
                custom
                if os.path.isabs(custom)
                else os.path.join(os.path.dirname(os.path.abspath(__file__)), custom)
            )
        name = BUNDLED_REF_WORKFLOW if kind == "ref" else BUNDLED_T2I_WORKFLOW
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "workflows", name
        )

    def _load_workflow(self, kind: str) -> tuple[dict, WorkflowSpec]:
        """按角色加载兜底工作流。kind: t2i / ref。"""
        path = self._workflow_file(kind)
        label = "参考图" if kind == "ref" else "文生图"
        return self._read_workflow_file(path, label)

    def _read_workflow_file(self, path: str, label: str) -> tuple[dict, WorkflowSpec]:
        """读一个 API 格式工作流并反推语义。label 只用于报错信息。"""
        if not os.path.isfile(path):
            raise RuntimeError(f"找不到{label}工作流文件：{path}")
        try:
            with open(path, encoding="utf-8") as f:
                wf = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"{label}工作流文件读不了（{path}）：{e}") from e
        if not isinstance(wf, dict):
            raise RuntimeError(
                f"{label}工作流格式不对：顶层应该是「节点ID → 节点」的字典，"
                f"当前是 {type(wf).__name__}。请用 ComfyUI 的「导出（API）」"
                "重新导一份，不是界面里保存的那份。"
            )
        spec = detect_workflow(wf)
        if not spec.sampler_node or not spec.positive_field:
            raise RuntimeError(
                f"认不出这个{label}工作流"
                f"（{'；'.join(spec.notes) or '找不到采样器或提示词节点'}）：{path}\n"
                "必须是 ComfyUI「导出（API）」得到的 JSON。"
            )
        return wf, spec

    async def _comfy_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict | None = None,
        timeout: float | None = None,
    ) -> Any:
        """发一次 ComfyUI 请求并返回解析后的 JSON。"""
        kwargs: dict[str, Any] = {
            "timeout": aiohttp.ClientTimeout(
                total=timeout if timeout else self.comfy_timeout, connect=10
            )
        }
        if payload is not None:
            kwargs["json"] = payload
        try:
            async with self.session.request(method, url, **kwargs) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(
                        explain_http_error(resp.status, body, self._comfy_base())
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise RuntimeError(explain_exception(e, self._comfy_base())) from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"ComfyUI 返回了非 JSON 内容：{body[:200]}") from e

    async def _comfy_upload(self, path: str) -> str:
        """把参考图上传到 ComfyUI 的 input 目录，返回它给的文件名。

        ComfyUI 的 LoadImage 只认「input 目录里的文件名」，所以必须先上传。
        """
        filename = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except OSError as e:
            raise RuntimeError(f"读参考图失败（{path}）：{e}") from e

        form = aiohttp.FormData()
        form.add_field("image", blob, filename=filename, content_type="image/*")
        form.add_field("overwrite", "true")
        form.add_field("type", "input")
        try:
            async with self.session.post(
                upload_endpoint(self._comfy_base()),
                data=form,
                timeout=aiohttp.ClientTimeout(total=120, connect=10),
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(
                        f"参考图上传失败（HTTP {resp.status}）：{body[:200]}"
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise RuntimeError(explain_exception(e, self._comfy_base())) from e
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"上传接口返回非 JSON：{body[:200]}")
        name = str(data.get("name") or "") if isinstance(data, dict) else ""
        if not name:
            raise RuntimeError(f"上传接口没返回文件名：{body[:200]}")
        return name

    async def _comfy_submit(self, wf: dict) -> str:
        data = await self._comfy_json(
            "POST",
            prompt_endpoint(self._comfy_base()),
            payload={"client_id": f"astrbot-{uuid.uuid4().hex[:12]}", "prompt": wf},
            timeout=60,
        )
        prompt_id = str(data.get("prompt_id") or "") if isinstance(data, dict) else ""
        if not prompt_id:
            raise RuntimeError(f"提交任务成功但没拿到 prompt_id：{str(data)[:200]}")
        return prompt_id

    async def _comfy_wait(self, prompt_id: str) -> list[OutputImage]:
        """轮询 /history 直到任务结束，返回输出图。"""
        deadline = time.time() + self.comfy_timeout
        base = self._comfy_base()
        while time.time() < deadline:
            await asyncio.sleep(self.comfy_poll_interval)
            history = await self._comfy_json(
                "GET", history_endpoint(base, prompt_id), timeout=30
            )
            record = history.get(prompt_id) if isinstance(history, dict) else None
            state, detail = history_status(record)
            if state == "error":
                raise RuntimeError(f"ComfyUI 执行失败：{detail}")
            if state == "completed":
                images = extract_images(record)
                if not images:
                    raise RuntimeError(
                        "ComfyUI 跑完了但没有输出图片（保存节点被禁用了？）"
                    )
                return images
        raise RuntimeError(
            f"等 ComfyUI 出图超时（{self.comfy_timeout:.0f} 秒，prompt_id={prompt_id}）。"
            "多半是显存不够、或队列里还排着别的任务。"
        )

    async def _run_local_workflow(
        self,
        kind: str,
        prompt: str,
        ref_local_path: str | None,
        count: int,
        entry: WorkflowEntry | None = None,
    ) -> tuple[list[str], list[str]]:
        """跑一次本地工作流，返回 (图片URL列表, 说明列表)。

        entry 非空 = 跑清单里模型挑的那套预设；为空 = 跑插件自带的兜底工作流。
        """
        if entry is not None:
            wf, spec, err = self._entry_info(entry)
            if wf is None or spec is None:
                raise RuntimeError(err or f"工作流「{entry.name}」加载失败")
            label = entry.name
        else:
            wf, spec = self._load_workflow(kind)
            label = os.path.basename(self._workflow_file(kind))

        notes: list[str] = []

        ref_name = ""
        if ref_local_path:
            if not spec.supports_reference:
                raise RuntimeError(
                    f"工作流「{label}」结构上不吃参考图"
                    "（没有已连到文本编码节点的 LoadImage）"
                )
            ref_name = await self._comfy_upload(ref_local_path)

        seed = self.seed if self.seed >= 0 else random.randint(1, 2**31 - 1)
        # 带 batch_size 字段的工作流才谈得上一次出多张（那些靠 EmptyLatentImage
        # 出画布的）；画布跟着参考图走的没有这个字段。
        batch = count if spec.batch_field else None
        new_wf, apply_notes = apply_params(
            wf,
            spec,
            prompt=prompt,
            negative_prompt=self.comfy_negative or None,
            seed=seed,
            # steps=None = 不覆盖，沿用工作流 JSON 自带的值（精准版 35 / 美感版 25）
            steps=self.comfy_steps or None,
            width=self.comfy_width,
            height=self.comfy_height,
            batch_size=batch,
            resolution=self.comfy_resolution,
            ref_image_name=ref_name or None,
        )
        notes.extend(apply_notes)

        base = self._comfy_base()
        logger.info(
            f"[{PLUGIN_NAME}] 本地工作流「{label}」：{spec.describe()}"
            f" | seed={seed}"
            + (f" steps={self.comfy_steps}" if self.comfy_steps else " steps=跟随工作流")
            + (
                f" | 参考图 {os.path.basename(ref_local_path)}"
                if ref_local_path
                else ""
            )
        )
        prompt_id = await self._comfy_submit(new_wf)
        images = await self._comfy_wait(prompt_id)
        notes.append(f"（本地 ComfyUI「{label}」seed={seed}）")
        return [img.endpoint(base) for img in images], notes

    async def _generate_local(
        self,
        prompt: str,
        ref_local_path: str | None,
        count: int,
        workflow: str = "",
    ) -> tuple[list[str], list[str]]:
        """本地生成。工作流选择 + 「参考图工作流失败 → 退回纯文本」的降级。

        workflow 是模型点名的工作流名（清单里的那个），留空 = 由插件按需求自己挑。
        「参考图失败退回纯文本」是用户明确要的：参考图工作流一旦报错（参考图不对、
        工作流被改动、自定义节点缺失…），不要让整次请求失败，而是丢掉参考图重来。
        """
        entry, pick_note = self._pick_local_workflow(workflow, need_ref=bool(ref_local_path))
        extra = [pick_note] if pick_note else []

        if ref_local_path:
            try:
                urls, notes = await self._run_local_workflow(
                    KIND_REF, prompt, ref_local_path, count, entry=entry
                )
                return urls, extra + notes
            except Exception as e:
                if not self.comfy_ref_fallback:
                    raise
                logger.warning(
                    f"[{PLUGIN_NAME}] 参考图工作流失败，改用纯文本工作流重新生成。原因：{e}"
                )
                # 重来时已经不带参考图了，也就不该再点名那套参考图工作流
                t2i_entry, _n = self._pick_local_workflow(workflow, need_ref=False)
                urls, notes = await self._run_local_workflow(
                    KIND_T2I, prompt, None, count, entry=t2i_entry
                )
                notes.insert(
                    0,
                    "（参考图工作流报错，已改用纯文本重新生成 —— 本次没有用参考图。"
                    f"原因：{str(e)[:140]}）",
                )
                return urls, extra + notes

        urls, notes = await self._run_local_workflow(
            KIND_T2I, prompt, None, count, entry=entry
        )
        return urls, extra + notes

    async def _probe_comfy(self) -> str:
        """本地 ComfyUI 自检：连通性 / 版本 / 显存 / 队列 / 工作流节点齐不齐。"""
        base = self._comfy_base()
        lines = [f"🖥️ 本地 ComfyUI：{base}"]
        try:
            stats = await self._comfy_json(
                "GET", system_stats_endpoint(base), timeout=10
            )
        except Exception as e:
            lines.append(f"❌ 连不上：{e}")
            lines.append(
                "   → 确认 ComfyUI 已启动；若是 Docker，注意端口映射后的地址。"
            )
            return "\n".join(lines)

        lines.append("✅ " + summarize_system_stats(stats))
        try:
            queue = await self._comfy_json("GET", queue_endpoint(base), timeout=10)
            lines.append("队列：" + summarize_queue(queue))
        except Exception:
            pass

        try:
            object_info = parse_object_info(
                await self._comfy_json("GET", object_info_endpoint(base), timeout=30)
            )
        except Exception as e:
            lines.append(f"⚠️ 取不到节点清单（{e}），跳过工作流校验。")
            return "\n".join(lines)

        # ---- 可选清单（模型能自己挑的那些）----
        lines.append(f"可选工作流：{self.comfy_workflow_dir}")
        if not self.comfy_workflow_select:
            lines.append("   ⚠️ comfyui_workflow_select 已关闭 —— 不会交给模型自选。")
        menu = self._workflow_menu()
        if menu:
            for entry, spec in menu:
                wf_e, _s, _e = self._entry_info(entry)
                missing = missing_node_types(wf_e or {}, object_info)
                tag = "支持参考图" if spec.supports_reference else "纯文生图"
                lines.append(
                    f"{'❌' if missing else '✅'} {entry.name}（{tag}）："
                    f"{os.path.basename(entry.path)}"
                )
                lines.append(f"   {spec.describe()}")
                if missing:
                    lines.append(
                        f"   缺节点：{'、'.join(missing)}（请在 ComfyUI 里装好对应插件）"
                    )
        for entry in self._local_workflows():
            wf_b, _s, verr = self._entry_info(entry)
            if wf_b is None:
                lines.append(
                    f"❌ {entry.name}（{os.path.basename(entry.path)}）：{str(verr)[:160]}"
                )
        if not menu:
            lines.append("   （没有可用工作流，将只用下面的兜底工作流）")

        lines.append("")
        lines.append("兜底工作流（清单为空时使用）：")
        for kind, label in (("t2i", "文生图"), ("ref", "参考图")):
            try:
                wf, spec = self._load_workflow(kind)
            except Exception as e:
                lines.append(f"❌ {label}工作流：{e}")
                continue
            missing = missing_node_types(wf, object_info)
            lines.append(
                f"{'✅' if not missing else '❌'} {label}工作流："
                f"{os.path.basename(self._workflow_file(kind))}"
            )
            lines.append(f"   {spec.describe()}")
            if missing:
                lines.append(
                    f"   缺节点：{'、'.join(missing)}（请在 ComfyUI 里装好对应插件）"
                )
            if kind == "ref" and not spec.supports_reference:
                lines.append("   该工作流结构上不吃参考图，会自动退化成纯文生图。")
        return "\n".join(lines)

    # ==================================================================
    # 内容审查（v2.0.0）
    # ==================================================================
    def _available_providers(self) -> list[tuple[str, str]]:
        """列出可用于审查的对话模型 [(Provider ID, 显示名)]。"""
        out_list: list[tuple[str, str]] = []
        try:
            providers = self.context.get_all_providers() or []
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] 列举 Provider 失败：{e}")
            return out_list
        for p in providers:
            try:
                meta = p.meta()
                pid = str(getattr(meta, "id", "") or "")
                model = str(getattr(meta, "model", "") or "")
            except Exception:
                continue
            if not pid:
                continue
            out_list.append((pid, f"{pid}（{model}）" if model else pid))
        return out_list

    async def _moderation_provider(self, event: AstrMessageEvent):
        """拿审查用的模型：先按配置的 ID，找不到再退到当前会话默认对话模型。"""
        if self.moderation_provider_id:
            prov = self.context.get_provider_by_id(self.moderation_provider_id)
            if prov is not None:
                return prov
            logger.warning(
                f"[{PLUGIN_NAME}] 配置的审查模型 {self.moderation_provider_id} 找不到，"
                "改用当前会话的默认对话模型（可用清单见 /画图诊断）。"
            )
        try:
            return await self.context.get_using_provider_async(event.unified_msg_origin)
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] 取默认对话模型失败：{e}")
            return None

    async def _moderate(
        self,
        event: AstrMessageEvent,
        *,
        stage: str,
        user_text: str,
        system_text: str,
        image_paths: list[str] | None,
    ) -> Verdict:
        """统一入口：调模型 → 解析判定。任何异常都变成 ok=False，绝不炸主流程。"""
        prov = await self._moderation_provider(event)
        if prov is None:
            return Verdict(
                ok=False,
                raw="没有可用的审查模型（未配置或未启用 Provider）",
                stage=stage,
            )
        try:
            resp = await asyncio.wait_for(
                prov.text_chat(
                    prompt=user_text,
                    image_urls=list(image_paths) if image_paths else None,
                    system_prompt=system_text,
                ),
                timeout=self.moderation_timeout,
            )
        except asyncio.TimeoutError:
            return Verdict(
                ok=False,
                raw=f"审查模型超时（>{self.moderation_timeout:.0f}s）",
                stage=stage,
            )
        except Exception as e:
            return Verdict(
                ok=False, raw=f"调用审查模型失败：{str(e)[:200]}", stage=stage
            )

        text = str(getattr(resp, "completion_text", "") or "")
        verdict = parse_verdict(text, stage=stage)
        logger.info(
            f"[{PLUGIN_NAME}] 审查（{stage}）→ {verdict.label}"
            + (f"｜类别={verdict.category}" if verdict.category else "")
            + (f"｜理由={verdict.reason}" if verdict.reason else "")
        )
        return verdict

    async def _moderate_text(self, prompt: str, event: AstrMessageEvent) -> Verdict:
        """生成前审查提示词（纯文本调用）。"""
        return await self._moderate(
            event,
            stage="prompt",
            user_text=build_prompt_check_user(
                prompt, max_chars=self.moderation_max_chars
            ),
            system_text=build_prompt_check_system(self.moderation_strict),
            image_paths=None,
        )

    async def _moderate_image(
        self, path: str, prompt: str, event: AstrMessageEvent
    ) -> Verdict:
        """生成后审查图片（把本地图片交给多模态模型看）。"""
        return await self._moderate(
            event,
            stage="image",
            user_text=build_image_check_user(prompt),
            system_text=build_image_check_system(self.moderation_strict),
            image_paths=[path],
        )

    def _moderation_report(self) -> str:
        if not self.moderation_enabled:
            return "🔒 内容审查：已关闭 ⚠️（本地生成会无审核直出，慎用）"
        checks = []
        if self.moderation_check_prompt:
            checks.append("生成前查提示词")
        if self.moderation_check_image:
            checks.append("生成后查图片")
        prov = None
        if self.moderation_provider_id:
            try:
                prov = self.context.get_provider_by_id(self.moderation_provider_id)
            except Exception:
                prov = None
        if self.moderation_provider_id and prov is None:
            state = f"⚠️ 配置的 {self.moderation_provider_id} 不存在，实际用会话默认模型"
        elif prov is not None:
            state = f"✅ {self.moderation_provider_id}"
        else:
            state = "会话默认对话模型（未显式指定）"
        return (
            f"🔒 内容审查：已开启（{'、'.join(checks) or '两道都没开'}）\n"
            f"   识图模型：{state}\n"
            f"   拿不准时：{'按不安全处理' if self.moderation_strict else '按安全处理'}｜"
            f"审不了时：{'放行' if self.moderation_fail_open else '拦截'}"
        )

    def _provider_report(self) -> str:
        items = self._available_providers()
        if not items:
            return "🤖 可用对话模型：一个都没有（请先在「服务提供商」里配好）"
        lines = [
            "🤖 可用对话模型（把 ID 填进 moderation_provider_id 即可当识图模型）："
        ]
        for pid, label in items:
            mark = "  ← 当前审查模型" if pid == self.moderation_provider_id else ""
            lines.append(f"   · {label}{mark}")
        lines.append(
            "   注：只有本身支持图片输入的模型才审得了图；纯文本模型会在「图片审查」"
            "那一步报错，插件按「审不了」处理（默认放行）。"
        )
        return "\n".join(lines)

    # ==================================================================
    # 核心：生成
    # ==================================================================
    async def _do_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        mode: str = "auto",
        size: str | None = None,
        count: int | None = None,
        ref_source: str = REF_SOURCE_AUTO,
        workflow: str = "",
        arg_llm_tip: str = "",
        arg_user_tip: str = "",
    ) -> tuple[bool, str, str]:
        """生成并直接发送图片。

        mode: auto（自动判断）/ text（强制文生图）/ image（强制图生图）
        ref_source: auto / persona / message / none —— 参考图从哪来
        workflow: 本地工作流名（清单里的那个），留空 = 插件按需求自己挑
        arg_llm_tip / arg_user_tip: 「工具参数容错层」的说明文本。只有 LLM 工具入口
            会传；指令入口不传。调用方与用户都收不到时留空（= 参数一切正常）。

        返回 (是否成功, 给 LLM 的结果文本, 给用户的回执文本)。
        """
        if ref_source not in REF_SOURCES:
            ref_source = REF_SOURCE_AUTO
        try:
            ok, llm, user = await self._run_generate(
                event, prompt, mode, size, count, ref_source, workflow
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 生图异常：{e}", exc_info=True)
            return False, f"生图失败：{e}", f"❌ 生图失败：{e}"
        # 参数容错层的话必须跟着**每一条**返回路径走 —— 否则「模型自己发明参数名」
        # 这件事又会变成一次静默降级，而那正是 v2.0.2 要根治的东西。
        if arg_llm_tip:
            llm = f"{llm}{arg_llm_tip}"
        if arg_user_tip and user:
            user = f"{user}\n{arg_user_tip}"
        return ok, llm, user

    async def _run_generate(
        self,
        event: AstrMessageEvent,
        prompt: str,
        mode: str,
        size: str | None,
        count: int | None,
        ref_source: str = REF_SOURCE_AUTO,
        workflow: str = "",
    ) -> tuple[bool, str, str]:
        # v2.0.0：api_key 不再是硬门槛 —— backend=local_only 时纯本地跑，不需要 Key。
        # 真正需要 Key 的那一步（调云端）会自己再检查一次。

        # ---- 提示词（参数没给就回捞用户自己说的文本）----
        prompt = (prompt or "").strip()
        if not prompt:
            prompt = strip_command_prefix(self._own_text(event), KNOWN_COMMANDS)

        # ---- 参考图：消息附图 + 人格参考图，由 ref_source 决定用哪个 ----
        msg_refs: list[str] = []
        if mode != "text" and self.allow_reference:
            msg_refs = await self._collect_reference_images(event)

        persona_path: str | None = None
        persona_how = ""
        persona_name = ""
        if mode != "text":
            persona_path, persona_name, persona_how = await self._persona_ref_for(event)

        use_persona = False
        use_msg = False
        # 「自动」模式下没找到人格图会静默退成文生图 —— 用户与 AI 都收不到任何信号，
        # 于是就会以为「插件只会文生图」。这两个变量把原因显式带出去。
        ref_miss = ""
        ref_miss_llm = ""
        if ref_source == REF_SOURCE_NONE or mode == "text":
            pass
        elif ref_source == REF_SOURCE_PERSONA:
            if not persona_path:
                who = f"（当前人格：{persona_name}）" if persona_name else ""
                why = f"\n原因：{persona_how}" if persona_how else ""
                msg = (
                    f"❌ 你要求用「当前 AI 人格」的参考图，但没找到对应文件{who}。"
                    f"{why}\n"
                    f"请把图片放进 {self._refs_dir()}，文件名 = 人格名（例如 `橙橘.png`）；\n"
                    "名字对不上时用配置项 reference_alias_map 写一行 `人格名=文件名`。\n"
                    "发 /画图参考 可以看到目录现状。"
                )
                return False, msg, msg
            use_persona = True
        elif ref_source == REF_SOURCE_MESSAGE:
            if not msg_refs:
                msg = (
                    "❌ 你要求用消息里附带的图片作参考，但这次消息里没有可用的图。\n"
                    "请把图片和指令发在同一条消息里，或引用一条带图的消息。"
                )
                return False, msg, msg
            use_msg = True
        else:  # auto：按配置的优先级自动挑
            if self.reference_priority == "message":
                use_msg = bool(msg_refs)
                use_persona = bool(persona_path) and not use_msg
            else:
                use_persona = bool(persona_path)
                use_msg = bool(msg_refs) and not use_persona

        if (
            ref_source == REF_SOURCE_AUTO
            and mode != "text"
            and not use_persona
            and not use_msg
        ):
            # 一条不落地交代清楚：谁被当成了当前人格、目录里有什么、下一步怎么改
            if persona_name:
                _files = list_ref_files(self._refs_dir())
                if _files:
                    ref_miss = (
                        f"\n⚠️ 本次没有用上人格参考图：当前会话人格是「{persona_name}」，"
                        f"但 {self._refs_dir()} 里没有对应文件"
                        f"（现有 {len(_files)} 张："
                        + "、".join(os.path.basename(p) for _s, p in _files[:5])
                        + "）。已按纯文生图处理，人物长相可能与本人不一致。"
                    )
                    _hint = suggest_text(self._refs_dir(), persona_name)
                    if _hint:
                        ref_miss += "\n" + _hint
                    ref_miss_llm = (
                        f"\n（提醒：本次没有用参考图 —— 当前人格「{persona_name}」在 "
                        "参考图目录里没有对应文件。可以顺口告诉用户发 /画图参考 看该怎么放图。）"
                    )
                else:
                    ref_miss = f"\n⚠️ 本次没有用上人格参考图：目录 {self._refs_dir()} 里一张图都没有。"
                    ref_miss_llm = "\n（提醒：本次没有用参考图 —— 参考图目录是空的。）"
            else:
                ref_miss = (
                    "\n⚠️ 本次没有用上人格参考图：插件没取到当前会话的人格名"
                    "（发 /画图参考 看解析结果）。已按纯文生图处理。"
                )
                ref_miss_llm = (
                    "\n（提醒：本次没有用参考图 —— 没解析出当前会话的人格名。）"
                )

        logger.info(
            f"[{PLUGIN_NAME}] 参考图决策：source={ref_source}｜"
            f"人格={persona_name or '（无）'}｜人格图={'用' if use_persona else '不用'}"
            f"｜消息图={len(msg_refs) if use_msg else 0} 张"
            f"｜最终={'图生图' if (use_persona or use_msg) else '文生图'}"
        )

        # 云端形态的参考图（data URI / 直链）
        refs: list[str] = []
        if use_persona and persona_path:
            uri = self._file_to_data_uri(persona_path)
            if uri:
                refs.append(uri)
            else:
                # 云端形态（data URI）拿不到：文件读不出、是空的、或超单张上限。
                # **不能**顺手把 use_persona 抹掉 —— 本地那条链路用的是文件路径，
                # 跟 data URI 无关；抹掉会连累 backend=local_first / local_only 的
                # 用户一起用不上参考图。这里只记一笔，云端那一步自然少一张图。
                logger.warning(
                    f"[{PLUGIN_NAME}] 人格参考图无法转成 data URI（{persona_path}），"
                    "云端那一路将不带参考图；本地不受影响。"
                )
        if use_msg:
            refs.extend(msg_refs)
        refs = refs[: self.max_reference_images]

        # 本地形态的参考图（ComfyUI 的 LoadImage 只吃 input 目录里的**单个**文件）
        local_ref: str | None = None
        ref_note = ""
        if use_persona and persona_path:
            local_ref = persona_path
            ref_note = f"（使用人格参考图：{persona_how}）"
        elif use_msg and msg_refs:
            local_ref = await self._download_to_file(msg_refs[0])
            if local_ref:
                ref_note = "（使用消息里附带的图片作参考）"

        is_i2i = bool(refs) or bool(local_ref)

        if mode == "image" and not is_i2i:
            msg = (
                "❌ 没有检测到可用的参考图。请先发一张图（或引用一张带图的消息），"
                f"或在 {self._refs_dir()} 里放一张以当前人格命名的图片，"
                "也可以用 /画图 让插件自动判断。"
            )
            return False, msg, msg

        if not prompt and not is_i2i:
            msg = "❌ 请描述你想要的画面，例如：/画图 一只戴墨镜的橘猫坐在海边看日落，超写实摄影风格"
            return False, msg, msg

        if not prompt:
            prompt = self.default_reference_prompt

        pre_note = ""

        # ---- 限流 / 防重 ----
        user_id = event.get_sender_id()
        if user_id in self._processing:
            msg = "⏳ 你还有一张图在生成中，请稍等一下~"
            return False, msg, msg
        if self.rate_limit > 0:
            now = time.time()
            last = self._last_ops.get(user_id, 0.0)
            if now - last < self.rate_limit:
                wait = int(self.rate_limit - (now - last)) + 1
                msg = f"⏳ 操作太快啦，请 {wait} 秒后再试~"
                return False, msg, msg
            self._last_ops[user_id] = now

        # ---- 审查①：生成前查提示词 ----
        # 云端方舟自带审核，本地 ComfyUI 完全没有 —— 而本地跑的恰恰是能出 R18 的
        # 开源模型，所以这道闸门必须自己挡。放在花钱之前：拦下来就一分钱不花。
        if self.moderation_enabled and self.moderation_check_prompt:
            v = await self._moderate_text(prompt, event)
            if v.flagged:
                logger.warning(
                    f"[{PLUGIN_NAME}] 提示词未通过审查，已拦截："
                    f"{v.category or ''} {v.reason}"
                )
                return (
                    False,
                    f"提示词被审查拦截，未生成图片。（{v.reason or v.category or '判为 R18'}）",
                    verdict_receipt(v, action="block"),
                )
            if not v.ok:
                receipt = unclear_receipt(v, fail_open=self.moderation_fail_open)
                if not self.moderation_fail_open:
                    return (
                        False,
                        "提示词审查未得出结论，按配置（fail_open=false）拦截。",
                        receipt,
                    )
                pre_note = receipt
        # 注：每人每日张数上限只对**云端**生效（本地生成不花钱，不该被额度卡住），
        # 所以这道闸门挪到了真正要调云端接口的那一步。

        full_prompt = self._compose_prompt(prompt)

        # ---- v2.0.3 槽位适配：同一套模板喂两个后端 ----
        # LLM 只写 <imageN>：本地 Qwen 原样保留，云端 Seedream 翻成自然语言；
        # 序号超出本次实际参考图张数的（悬空引用）按句删掉，并如实告知两边。
        local_prompt, cloud_prompt, slot_llm, slot_zh = self._adapt_ref_slots(
            full_prompt,
            local_ref_count=1 if local_ref else 0,
            cloud_ref_count=len(refs),
        )

        urls: list[str] | None = None
        note = ref_note + pre_note + ref_miss_llm + slot_llm
        local_error = ""
        used_backend = ""
        used_model = self.model
        payload: dict = {}
        fingerprint = ""
        effective = self.count if count is None else max(1, int(count))

        # ---- 后端调度：本地优先 → 云端兜底 ----
        if self.backend in (BACKEND_LOCAL_FIRST, BACKEND_LOCAL_ONLY):
            try:
                logger.info(
                    f"[{PLUGIN_NAME}] 用户 {user_id} 请求生图（本地）| "
                    f"参考图={'有' if local_ref else '无'} | 提示词长度 {len(full_prompt)}"
                )
                async with self._sem:
                    urls, local_notes = await self._generate_local(
                        local_prompt, local_ref, effective, workflow=workflow
                    )
                used_backend = BACKEND_LOCAL
                note += "".join(local_notes)
            except Exception as e:
                local_error = str(e)
                logger.warning(f"[{PLUGIN_NAME}] 本地生成失败：{local_error}")
                urls = None
                if self.backend == BACKEND_LOCAL_ONLY:
                    tail = (
                        ""
                        if not self.api_key
                        else "（当前 backend=local_only，不会走云端兜底）"
                    )
                    text = f"❌ 本地生图失败：{local_error}{tail}"
                    return False, text, text

        self._processing.add(user_id)
        try:
            if urls is None:
                # ---- 云端：火山方舟 ----
                if not self.api_key:
                    text = "❌ 云端生图不可用：插件未配置火山方舟 API Key。"
                    if local_error:
                        text = (
                            f"❌ 本地生图失败：{local_error}\n"
                            "云端兜底也不可用：未配置 api_key。"
                        )
                    return False, text, text

                if self.daily_limit > 0:
                    used = self._daily_used(user_id)
                    if used >= self.daily_limit:
                        msg = (
                            f"🚫 今天的云端生图额度已用完（{used}/{self.daily_limit} 张）。"
                            "明天会自动重置；如需调整请改插件配置里的 daily_limit。"
                        )
                        return False, msg, msg

                payload, effective, cb_note = self._build_payload(
                    cloud_prompt, refs, size, count
                )
                note += cb_note

                # ---- 省钱护栏 2：同一请求短期内复用已生成的图（不请求上游 = 不花钱）----
                fingerprint = self._fingerprint(payload)
                if self.cache_seconds > 0:
                    hit = self._cache.get(fingerprint)
                    if (
                        hit
                        and time.time() - hit[1] <= self.cache_seconds
                        and os.path.exists(hit[0])
                    ):
                        logger.info(
                            f"[{PLUGIN_NAME}] 命中本地缓存，直接复用已有图片并重发，"
                            "未调用上游接口（本次约 0 元）。"
                        )
                        try:
                            await event.send(
                                event.chain_result([Image.fromFileSystem(hit[0])])
                            )
                            return (
                                True,
                                "已发送 1 张图片（命中本地缓存，未重新生成、未产生费用）。"
                                "请用一两句自然语言告诉用户图片已发送。",
                                self.success_text,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[{PLUGIN_NAME}] 缓存图片发送失败，改为重新生成：{e}"
                            )
                            self._cache.pop(fingerprint, None)
                    elif hit:
                        self._cache.pop(fingerprint, None)

                logger.info(
                    f"[{PLUGIN_NAME}] 用户 {user_id} 请求生图（云端）| "
                    f"模式={'图生图' if is_i2i else '文生图'} "
                    f"| 参考图 {len(refs)} 张 | 目标 {effective} 张 | "
                    f"提示词长度 {len(full_prompt)}"
                )
                logger.info(f"[{PLUGIN_NAME}] 请求体：{self._payload_for_log(payload)}")
                used_model = self.model
                async with self._sem:
                    try:
                        urls = await self._call_api(payload)
                    except ModelRoutingError as e:
                        # 模型无法路由到接入点（未开通 / 需要专属端点）。
                        # 失败请求不计费，所以回退一次不会多花钱，
                        # 但能保证用户不会因为选了个没开通的模型而彻底生不出图。
                        if (
                            not self.auto_fallback
                            or not self.fallback_model
                            or self.fallback_model == used_model
                        ):
                            raise
                        logger.warning(
                            f"[{PLUGIN_NAME}] 模型 {used_model} 在本账号下不可用"
                            f"（未开通 / 无法路由到接入点），"
                            f"按 auto_fallback 改用 {self.fallback_model} 重试一次"
                            f"（失败请求不计费）。原因：{e}"
                        )
                        fb_payload, effective, fb_note = self._build_payload(
                            cloud_prompt, refs, size, count, model=self.fallback_model
                        )
                        urls = await self._call_api(fb_payload)
                        payload = fb_payload
                        fingerprint = self._fingerprint(payload)
                        note += fb_note + (
                            f"（原模型 {used_model} 无法出图，已自动改用 "
                            f"{self.fallback_model} 生成）"
                        )
                        used_model = self.fallback_model
                        logger.info(
                            f"[{PLUGIN_NAME}] 已回退到 {used_model} 并成功生成。"
                            f"想真用 {self.model}，{ACTIVATION_HINT}。"
                        )
                used_backend = BACKEND_CLOUD
                if local_error:
                    note += f"（本地生成失败：{local_error}；已改用云端兜底）"

            components: list[Any] = []
            local_paths: list[str] = []
            for u in urls:
                try:
                    comp, local = await self._materialize(u)
                    components.append(comp)
                    if local:
                        local_paths.append(local)
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 图片组件构造失败：{e}")
            if not components:
                msg = "❌ 图片已生成但发送失败（无法获取图片内容）。"
                return False, msg, msg

            # ---- 审查②：生成后查图片 ----
            # 关键时序：这里是**发送之前**。拦下来图片就不会发出去。
            if self.moderation_enabled and self.moderation_check_image:
                review_target = local_paths[0] if local_paths else ""
                if not review_target and urls:
                    review_target = await self._download_to_file(urls[0]) or ""
                if review_target:
                    v = await self._moderate_image(review_target, prompt, event)
                    if v.flagged:
                        logger.warning(
                            f"[{PLUGIN_NAME}] 生成结果未通过审查，已拦截："
                            f"{v.category or ''} {v.reason}"
                        )
                        return (
                            False,
                            "生成结果被审查拦截，未发送。"
                            f"（{v.reason or v.category or '判为 R18'}）",
                            verdict_receipt(v, action="block"),
                        )
                    if not v.ok:
                        receipt = unclear_receipt(
                            v, fail_open=self.moderation_fail_open
                        )
                        if not self.moderation_fail_open:
                            return (
                                False,
                                "图片审查未得出结论，按配置（fail_open=false）拦截。",
                                receipt,
                            )
                        note += "\n" + receipt
                else:
                    logger.warning(
                        f"[{PLUGIN_NAME}] 取不到本地图片文件，跳过图片审查（按配置放行）。"
                    )

            # 图片下方是否附提示词（由 show_prompt 决定）
            if self.show_prompt and prompt:
                head = f"🖼️ {'图生图' if is_i2i else '文生图'}　提示词：{prompt}"
                if len(urls) > 1:
                    head += f"\n共 {len(urls)} 张"
                components.append(Plain(text=head))

            await event.send(event.chain_result(components))

            # ---- 记账：本次花费 / 累计张数 / 写入缓存 ----
            # daily_limit 只统计**云端**张数：本地生成不花钱，不该占用额度。
            is_cloud = used_backend == BACKEND_CLOUD
            used_size = str(payload.get("size") or self.size)
            spent = (
                unit_price(used_model, used_size, len(refs)) * len(urls)
                if is_cloud
                else 0.0
            )
            if is_cloud and self.daily_limit > 0:
                dk = self._daily_key(user_id)
                self._daily[dk] = self._daily.get(dk, 0) + len(urls)
            if is_cloud and self.cache_seconds > 0 and fingerprint and local_paths:
                self._cache_put(fingerprint, local_paths[0])
            logger.info(
                f"[{PLUGIN_NAME}] 本次由 {used_backend} 生成 {len(urls)} 张，"
                f"估算花费约 {spent:.2f} 元（{used_model} @ {used_size}）"
                + (
                    f"；今日该用户云端已用 "
                    f"{self._daily_used(user_id)}/{self.daily_limit} 张"
                    if is_cloud and self.daily_limit
                    else ""
                )
            )

            # ---- 给 LLM 的结果文本 ----
            kind = "图生图" if is_i2i else "文生图"
            llm = (
                f"已通过「{used_backend}」生成并发送 {len(urls)} 张图片（{kind}）。"
                "请用一两句自然语言告诉用户图片已发送。"
            )
            # 提示词只在 show_prompt=True 时才回灌给 LLM：
            # 工具结果是会进对话历史的，默认无脑附带等于把长提示词永久写进上下文
            # （既费 token，也会在下一次 /forget 之类的历史回显里暴露给用户）。
            if prompt and not is_i2i and self.show_prompt:
                llm += f"本次提示词：{prompt}。"
            if self.show_prompt:
                llm += "图片下方已附带提示词，不要重复粘贴提示词。"
            else:
                llm += "不要复述或粘贴提示词。"
            if note:
                llm += note

            # ---- 给用户的回执 ----
            if self.show_prompt:
                user_text = ""  # 提示词已随图片发出，不再另发一条
            else:
                user_text = self.success_text
                if note and not user_text:
                    user_text = note
            # 参考图没匹配上属于「用户必须知道」的事：不说清楚，
            # 他只会反复重试并怀疑是插件坏了。
            if slot_zh:
                user_text = (user_text + "\n" + slot_zh).strip()
            if ref_miss:
                user_text = (user_text + "\n" + ref_miss).strip()

            return True, llm, user_text
        finally:
            self._processing.discard(user_id)

    # ==================================================================
    # 省钱护栏 / 费用自检
    # ==================================================================
    @staticmethod
    def _daily_key(user_id: str) -> str:
        """按「日期 + 用户」计数，跨天自然失效。"""
        return f"{datetime.now().strftime('%Y-%m-%d')}|{user_id}"

    def _daily_used(self, user_id: str) -> int:
        return self._daily.get(self._daily_key(user_id), 0)

    def _daily_total(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d") + "|"
        return sum(v for k, v in self._daily.items() if k.startswith(today))

    @staticmethod
    def _fingerprint(payload: dict) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(blob.encode("utf-8")).hexdigest()

    def _cache_put(self, fingerprint: str, path: str):
        if len(self._cache) >= 50:  # 防止内存无限增长
            try:
                oldest = min(self._cache.items(), key=lambda kv: kv[1][1])[0]
                self._cache.pop(oldest, None)
            except ValueError:
                pass
        self._cache[fingerprint] = (path, time.time())

    def _mask_key(self) -> str:
        k = self.api_key
        if not k:
            return "（未配置）"
        return f"{k[:8]}…{k[-4:]}" if len(k) > 14 else "（已配置）"

    def _cost_line(self) -> str:
        per = unit_price(self.model, self.size)
        quota = free_quota_of(self.model)
        quota_txt = (
            f"官方免费额度 {quota} 张（用完会自动转付费）"
            if quota
            else "无官方免费额度（仅首张免费）"
        )
        return (
            f"💰 费用：约 {per:.2f} 元/张 × {self.count} 张 = {per * self.count:.2f} 元/次"
            f"｜{quota_txt}"
        )

    async def _plan_family_note(self) -> str:
        """**零成本**探测：该模型是否属于 Agent Plan / CodePlan 模型族。

        原理（2026-09-15 实测得出）：在 plan 端点上，「属于 plan 族」的模型会先通过
        模型校验、然后卡在鉴权（401 AuthenticationError，因为我们的 Key 是标准 Key）；
        「不属于」的模型会被 404 UnsupportedModel 提前拦下。
        请求同样不带 prompt，不生成图片、不产生费用。
        """
        try:
            status, body = await self._post_once(
                {"model": self.model}, url=AGENT_PLAN_URL
            )
        except Exception:
            return ""
        code, _ = self._parse_error(body)
        if status == 401 or code == "AuthenticationError":
            return "它属于 Agent Plan / CodePlan 模型族（标准 Key 走标准端点用不了）。"
        if status == 404 or code.startswith("UnsupportedModel"):
            return "它不属于 Agent Plan 模型族，只能走标准端点。"
        return ""

    async def _probe_account(self) -> tuple[str, str]:
        """**零成本**自检：故意不带 prompt。

        实测（2026-09-15）方舟的校验顺序是「参数完整性 → 模型/接入点解析 → 生成」，
        所以只带 model 的请求会停在第一步就失败（400 MissingParameter），
        **不会生成图片、不产生任何费用**。

        局限：正因为探针停在参数阶段，**「模型到底能不能真生成」这一步探不到**
        （lite 系列正是能过模型校验、真发请求必被上游网关拒绝）。
        所以额外查 MODEL_UNUSABLE 表，并用 _plan_family_note() 判定它属于哪条通道。
        """
        if not self.api_key:
            return "no_key", "❌ 未配置 API Key（插件配置里的 api_key）"
        try:
            status, body = await self._post_once({"model": self.model})
        except Exception as e:
            return "network", f"❌ 连不上火山方舟接口：{e}"

        code, msg = self._parse_error(body)
        if status == 400 and code.startswith("MissingParameter"):
            if self.model in MODEL_UNUSABLE:
                extra = await self._plan_family_note()
                fb = (
                    f"\n在此之前，插件会自动改用 {self.fallback_model} 出图（不会失败）。"
                    if self.auto_fallback and self.fallback_model != self.model
                    else "\n插件已关闭 auto_fallback，请求会直接报错。"
                )
                return "unusable", (
                    f"⚠️ Key 与账户都正常，但模型 {self.model} **无法路由到可用接入点**"
                    "（能通过模型存在性校验，真发请求必被上游网关拒绝；与参数、余额无关）。"
                    f"{extra}\n👉 开通后立刻可用：{ACTIVATION_HINT}。{fb}"
                )
            return "ok", "✅ 一切正常：Key 有效、模型可用、账户未欠费"
        if status == 401 or code == "AuthenticationError":
            return "auth", f"❌ API Key 无效（code={code}）"
        if looks_like_arrears(code, msg, status):
            return "arrears", "💳 账户欠费（余额不足）—— 请到火山方舟控制台充值"
        if looks_like_quota(code, msg):
            return "quota", "📉 额度已用尽（含免费额度）—— 请充值或换个模型"
        if status == 404 or code.startswith("InvalidEndpointOrModel"):
            return (
                "model",
                f"❌ 模型 {self.model} 不存在或未在本账号开通（code={code}）",
            )
        if status == 403:
            return "denied", f"❌ HTTP 403 被拒（多半是该模型未开通）：{msg[:80]}"
        return "other", f"⚠️ HTTP {status} code={code}：{msg[:80]}"

    async def _diagnose_text(self, event: AstrMessageEvent | None = None) -> str:
        tag, verdict = await self._probe_account()
        hint = {
            "ok": "可以正常生图了，直接发 /画图 <提示词> 即可。",
            "auth": "请到火山方舟控制台「API Key 管理」重新生成 Key 并填入插件配置。",
            "arrears": "这不是 Key 或配置问题 —— 充值后立刻恢复，插件无需任何改动。",
            "quota": "免费额度用完会自动转付费；建议去控制台「模型算力计费」设一个用量上限。",
            "model": (
                "把插件配置 model 改成在售模型（推荐 doubao-seedream-5-0-flash-260915，"
                "0.12 元/张）；若该模型也没开通，可直接用 doubao-seedream-4-0-250828。"
            ),
            "unusable": (
                f"{ACTIVATION_HINT}；或把 model / fallback_model 换成 "
                f"{MODEL_UNUSABLE.get(self.model, DEFAULT_MODEL)}。"
                "这不是 Key、余额或参数的问题。"
            ),
            "denied": "到控制台「开通管理 → 视觉模型」确认该模型已开通。",
            "network": "检查本机能否访问 ark.cn-beijing.volces.com。",
            "no_key": "填写 api_key 后重试。",
        }.get(tag, "把上面的 code 与原文发我，我来看。")
        if tag == "no_key" and self.backend != BACKEND_CLOUD_ONLY:
            hint = (
                "当前后端是「"
                + BACKEND_ZH.get(self.backend, self.backend)
                + "」，这个 Key 只用于云端兜底 —— 本地出图不依赖它，可以忽略。"
                + "想彻底不碰云端就把 backend 设成 local_only。"
                + "本地那一侧请看上面的「本地 ComfyUI」段落。"
            )
        cache_state = (
            f"{self.cache_seconds}s 内同提示词复用" if self.cache_seconds else "关闭"
        )
        endpoint = (
            "Agent Plan 专属端点"
            if "/api/plan/" in self.api_url
            else "标准端点 /api/v3"
        )
        fb_state = (
            f"开（改用 {self.fallback_model}）"
            if self.auto_fallback and self.fallback_model != self.model
            else "关"
        )
        try:
            comfy_part = await self._probe_comfy()
        except Exception as e:
            comfy_part = f"🖥️ 本地 ComfyUI：自检异常（{e}）"
        try:
            mod_part = self._moderation_report()
        except Exception as e:
            mod_part = f"🔒 内容审查：读取配置异常（{e}）"
        try:
            prov_part = self._provider_report()
        except Exception as e:
            prov_part = f"🤖 可用对话模型：读取失败（{e}）"
        try:
            if event is None:
                ref_part = "📂 参考图：跳过（没有会话上下文，无法解析人格）"
            else:
                ref_part = await self._refs_report(event)
        except Exception as e:
            ref_part = f"📂 参考图：读取失败（{e}）"

        return (
            f"🩺 生图自检（v{PLUGIN_VERSION}）\n\n"
            f"生成后端：{BACKEND_ZH.get(self.backend, self.backend)}\n"
            f"配置：model={self.model}｜size={self.size}｜格式={self.output_format}\n"
            f"能力：参考图 ≤ {self.caps_max_refs} 张｜"
            f"组图{'支持' if self.supports_group else '不支持'}"
            f"（参考图 + 生成图 ≤ {TOTAL_IMAGE_LIMIT}）｜"
            f"联网搜索{'支持' if self.supports_web_search else '不支持'}"
            f"（{'已开' if self.web_search else '未开'}）｜"
            f"提示词优化 {self.optimize_prompt_mode}"
            f"{'' if self.supports_fast_prompt else '（本模型不支持 fast）'}\n"
            f"接入点：{endpoint}\n"
            f"模型路由失败时自动回退：{fb_state}\n"
            f"Key：{self._mask_key()}\n"
            f"{self._cost_line()}\n"
            f"今日已生成：{self._daily_total()} 张"
            f"（每人日限额：{self.daily_limit if self.daily_limit else '不限'}，"
            f"缓存：{cache_state}）\n\n"
            f"{comfy_part}\n\n"
            f"{mod_part}\n"
            f"{ref_part}\n"
            f"{prov_part}\n\n"
            f"探测结果：{verdict}\n"
            f"建议：{hint}\n\n"
            "ℹ️ 本自检为零成本操作：请求故意不带 prompt，只走到参数校验就失败，"
            "不会生成图片、不产生费用。"
        )

    # ==================================================================
    # 指令
    # ==================================================================
    async def _command_flow(self, event: AstrMessageEvent, arg: str, mode: str):
        ok, _llm, user_text = await self._do_generate(event, arg or "", mode=mode)

        if ok:
            # 成功时图片已由 event.send() 直接发出，_has_send_oper 置位后
            # 管道不会再调用 LLM，无需额外 stop_event（保留 respond 阶段正常收尾）。
            if user_text:
                yield event.plain_result(user_text)
            return

        # 失败：必须「先 yield 再 stop_event」。
        # 顺序反了的话，调度器在 yield 点就发现事件已停止会直接 break，
        # 这条错误提示就永远发不出去；
        # 而 stop_event 又必不可少 —— 否则 star_request 会 clear_result，
        # 管道随后判定「无结果」从而让 LLM 接管指令、AI 开始编造回复。
        yield event.plain_result(user_text or "❌ 生图失败")
        event.stop_event()

    # 注意：参数类型必须是 GreedyStr，否则 AstrBot 只把「第一个词」当作参数，
    # `/画图 一只 戴墨镜的 橘猫` 会退化成「一只」——提示词被腰斩、钱还照花。
    @filter.command("画图")
    async def cmd_draw(self, event: AstrMessageEvent, arg: GreedyStr):
        async for r in self._command_flow(event, arg, "auto"):
            yield r

    @filter.command("生图")
    async def cmd_draw_alias(self, event: AstrMessageEvent, arg: GreedyStr):
        async for r in self._command_flow(event, arg, "auto"):
            yield r

    @filter.command("图生图")
    async def cmd_i2i(self, event: AstrMessageEvent, arg: GreedyStr):
        async for r in self._command_flow(event, arg, "image"):
            yield r

    @filter.command("文生图")
    async def cmd_t2i(self, event: AstrMessageEvent, arg: GreedyStr):
        async for r in self._command_flow(event, arg, "text"):
            yield r

    @filter.command("画图诊断")
    async def cmd_diagnose(self, event: AstrMessageEvent):
        """零成本自检：Key / 账户欠费 / 模型开通 / 费用 一次看清。"""
        try:
            text = await self._diagnose_text(event)
        except Exception as e:
            text = f"❌ 自检失败：{e}"
        yield event.plain_result(text)

    @filter.command("画图帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        yield event.plain_result(self._help_text())

    @filter.command("画图审查")
    async def cmd_moderation(self, event: AstrMessageEvent):
        """看内容审查的配置，以及哪些模型能当识图模型。"""
        try:
            text = "\n\n".join([self._moderation_report(), self._provider_report()])
        except Exception as e:
            text = f"❌ 读取审查配置失败：{e}"
        yield event.plain_result(text)

    @filter.command("画图参考")
    async def cmd_refs(self, event: AstrMessageEvent):
        """看人格参考图目录现状：谁有图、谁没图。"""
        try:
            text = await self._refs_report(event)
        except Exception as e:
            text = f"❌ 读取参考图目录失败：{e}"
        yield event.plain_result(text)

    # ==================================================================
    # LLM 工具
    # ==================================================================
    @llm_tool(name=LLM_TOOL_NAME)
    async def generate_image_tool(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        reference_source: str | None = None,
        workflow: str | None = None,
        size: str = None,
        count: int = None,
        **kwargs,
    ):
        """根据文字描述生成图片，并直接把图片发送给用户。

        凡是用户要求「画一张 / 生成图片 / 发张图给我 / 画个xxx / 帮我画 / 来张图 /
        换个背景」等需要产出图片的请求，都必须调用本工具，不要用文字描述画面来代替。

        ============================================================
        第一步：判断画面里有没有「你」—— 这一步决定 reference_source
        ============================================================
        · 有「你」→ persona
            明说：画你自己 / 你的样子 / 你的自拍 / 全身照 / 半身照 / 证件照 / 立绘 /
                  头像 / 换装 / 穿某件衣服的你 / 你在某个场景里 / 你和别人的合照；
            隐含（顺着上一张说）：再拍一张 / 换个姿势 / 换个背景 / 近一点 /
                  你现在的样子 / 多来几张。
            角色扮演里「你」= 你自己、「我」= 用户，不要弄反。
        · 用户这次带了图、且要以它为基准 → message（照着这张改 / 用这张当参考 /
          参考我刚发的图）。
        · 画面里既没有「你」也没给图 → none（画一只猫 / 一幅风景 / 某个物品 / 别人 / 梗图）。
        · 同时出现「画你」和「用户图」→ 主体优先，选 persona，用户图作换装/背景来源。
        · 拿不准 → 不传这个参数（默认 auto，插件按配置优先级挑）。
        没有明确理由不要选 none —— 选错会让画出来的人不像你，用户一眼看得出来。

        ============================================================
        第二步：写 prompt。有参考图时套下面的固定模板，只换 {变量}
        ============================================================
        保持 <image1> 里的人物身份完全不变，脸型和发型与 <image1> 完全一致。
        她现在{新场景}，{新动作}，{构图}。
        服装：{服装描述，可保留原服装，也可换成新服装}。
        不要用文字描述或重绘她的脸、眼睛、发色、五官。
        二次元赛璐璐风格，自然光线，高质量。

        变量填写规则：
        · {新场景}：纯地点 / 环境。例：咖啡店、夜晚的街道、樱花树下、浴室、卧室。
        · {新动作}：纯动作。例：拿着手机自拍、微笑看着镜头、泡澡、睡觉。
        · {构图}：全身照 / 半身照 / 自拍 / 特写 / 头像。
        · {服装描述}：
            - 保留原服装 → 「沿用 <image1> 中的服装」
            - 换装 → 只写服装本身。例：「白色浴袍」「黑色礼服」「宇航出舱服」
            - AI 可自主判断换装（洗澡、睡觉、出舱、节日、剧情需要），不必等用户明说
            - 换装时绝对不写脸和发型，那部分由「脸型和发型与 <image1> 完全一致」锁死
        · 禁止把用户原话整段塞进变量。
        · 禁止在变量里写「她是一个……的女孩」。

        示例一（不换装，咖啡店自拍）：
        保持 <image1> 里的人物身份完全不变，脸型和发型与 <image1> 完全一致。
        她现在在咖啡店里，拿着手机自拍，全身照。
        服装：沿用 <image1> 中的服装。
        不要用文字描述或重绘她的脸、眼睛、发色、五官。
        二次元赛璐璐风格，自然光线，高质量。

        示例二（AI 自主换装，洗澡场景）：
        保持 <image1> 里的人物身份完全不变，脸型和发型与 <image1> 完全一致。
        她现在在浴室里，泡在浴缸中，半身照。
        服装：白色浴袍，头发湿漉漉地贴在脸侧。
        不要用文字描述或重绘她的脸、眼睛、发色、五官。
        二次元赛璐璐风格，自然光线，高质量。

        示例三（用户指定换装）：
        用户说「画你穿这件衣服站在樱花树下」
        保持 <image1> 里的人物身份完全不变，脸型和发型与 <image1> 完全一致。
        她现在站在樱花树下，微笑，全身照。
        服装：白色毛衣和牛仔裤。
        不要用文字描述或重绘她的脸、眼睛、发色、五官。
        二次元赛璐璐风格，自然光线，高质量。

        ============================================================
        第三步：选工作流（只对本地 ComfyUI 生效，云端不区分）
        ============================================================
        可用清单见系统提示词里那一节（会随插件配置变化），**以那里为准**。
        不传 workflow = 插件自己挑（有参考图挑支持参考图的，否则挑默认的）。
        什么时候值得点名：
        · 有参考图 → 必须挑支持参考图的那套，否则参考图白给；
        · 用户点名要某个已知二次元角色（流萤、芙宁娜这类）且没有参考图 →
          挑「精准版」，并且**必须把 prompt 整段改写成英文**，它只吃英文提示词；
        · 画原创角色 / 日常场景 / 风景物品 → 挑「美感版」，快且稳；
        · 拿不准就**不要传**，让插件自己挑。

        ============================================================
        其它约定
        ============================================================
        · 参数名：控制参考图的参数只有 reference_source。不要写 use_reference_image /
          use_ref / reference / ref_source —— 写了插件也会归一化处理，但请直接写对。
        · 槽位：模板里的 <image1> / <image2> 交给插件翻译 —— 本地 Qwen 后端保留原样，
          云端 Seedream 后端自动换成自然语言。你始终只写 <imageN>，不要写两版。
        · 不要引用本次不存在的图：本次只有几张参考图，就只用 <image1>…<imageN>；
          引用了不存在的序号，那一句会被插件丢掉（白写）。
        Args:
            prompt(string): 画面描述。有参考图时按上面的固定模板写；纯文生图时尽量具体，
                包含主体、环境、风格、光线、构图；中英文均可。
            reference_source(string): 参考图来源。取值只有四个：
                persona = 用当前 AI 人格的参考图（画面主体是「你自己」就一律用这个）；
                message = 用本次消息里附带的图 / 引用回复的图；
                none = 完全不用参考图，纯文生图；
                不传（省略这个参数）= auto，插件按配置优先级自动挑。
            workflow(string): 可选，指定本地工作流名（就是系统提示词里列出的那些，
                如「美感版」「精准版」「参考版」）。只在使用本地 ComfyUI 时有效；
                云端不区分工作流。留空 = 插件按「有没有参考图」自动挑。
                名字写错不会静默换一套，插件会如实告诉你可选哪些。
            size(string): 可选，图片尺寸，默认跟随插件配置（通常 2K）。可选值：1K / 2K / 3K / 4K。
            count(number): 可选，生成数量，默认 1。仅部分模型支持多张，不支持时会自动降级为 1 张。
        """
        # 只有云端才需要 Key；本地 ComfyUI 不需要。
        if self.backend == BACKEND_CLOUD_ONLY and not self.api_key:
            return "生图功能未启用：backend=cloud_only 但插件未配置火山方舟 API Key。"

        # ---- v2.0.2 参数容错层 ----
        # 模型会自己发明参数名，实测抓到过 'use_reference_image': False。
        # 这里把常见的幻觉写法翻译回 reference_source，并把所有异常如实报出来，
        # 不再让「工具参数写错」变成一次无声的功能降级。
        raw_args = dict(kwargs)
        if reference_source is not None:
            raw_args[CANONICAL_REF_PARAM] = reference_source
        if workflow is not None:
            raw_args[CANONICAL_WORKFLOW_PARAM] = workflow
        norm = normalize_tool_args(raw_args, known=REAL_PARAMS)
        arg_llm_tip, arg_user_tip = describe_arg_fixes(norm)
        if norm.unknown_params:
            logger.warning(
                f"[{PLUGIN_NAME}] 工具收到未知参数 {norm.unknown_params}，已忽略。"
                f"本工具参数只有 {'/'.join(REAL_PARAMS)}。"
            )
        if norm.coerced:
            logger.info(
                f"[{PLUGIN_NAME}] 工具参数已归一化：{norm.used_param}="
                f"{norm.used_value!r} → reference_source={norm.ref_source}"
            )
        if norm.workflow_coerced:
            logger.info(
                f"[{PLUGIN_NAME}] 工作流参数已归一化：{norm.workflow_param} → "
                f"workflow={norm.workflow!r}"
            )

        mode = "text" if norm.ref_source == REF_SOURCE_NONE else "auto"
        _ok, llm, _user = await self._do_generate(
            event,
            coerce_str(prompt),
            mode=mode,
            size=coerce_size(size),
            count=coerce_int(count),
            ref_source=norm.ref_source,
            workflow=norm.workflow,
            arg_llm_tip=arg_llm_tip,
            arg_user_tip=arg_user_tip,
        )
        return llm

    # ==================================================================
    # 帮助
    # ==================================================================
    def _help_text(self) -> str:
        key_status = "已配置 ✅" if self.api_key else "❌ 未配置"
        if self.moderation_enabled:
            _chk = []
            if self.moderation_check_prompt:
                _chk.append("生成前查提示词")
            if self.moderation_check_image:
                _chk.append("生成后查图片")
            mod_line = "已开启（" + ("、".join(_chk) or "两道都未勾选，实际不查") + "）"
        else:
            mod_line = "❌ 已关闭（本地生成会无审核直出，慎用）"
        mod_prov = self.moderation_provider_id or "当前会话的默认对话模型"
        if self.persona_reference_enabled:
            _n_ref = len(list_ref_files(self._refs_dir()))
            _prio = (
                "消息附图优先" if self.reference_priority == "message" else "人格图优先"
            )
            persona_line = (
                f"已开启（目录 {self._refs_dir()}｜现有 {_n_ref} 张｜"
                f"自动挑选优先级：{_prio}）"
            )
        else:
            persona_line = "已关闭（只用消息里附带的图）"
        ref_status = (
            f"允许（{self.reference_transport}，最多 {self.max_reference_images} 张，"
            f"单张 ≤ {self.max_reference_mb:g}MB）"
            if self.allow_reference
            else "已禁用（只会文生图）"
        )
        prompt_echo = "会（图片下方显示提示词）" if self.show_prompt else "不会"
        if self.caps_sizes:
            size_scope = "/".join(sorted(self.caps_sizes))
        else:
            size_scope = "未限制（自定义模型）"
        cache_txt = (
            f"{self.cache_seconds} 秒内同提示词复用" if self.cache_seconds else "关闭"
        )
        _menu = self._workflow_menu()
        if not self.comfy_workflow_select:
            wf_line = "已关闭（一律按「有没有参考图」自动挑）"
        elif _menu:
            wf_line = "AI 可自选： " + "、".join(
                f"{e.name}{'（支持参考图）' if s.supports_reference else ''}"
                for e, s in _menu
            )
        else:
            wf_line = f"无可选预设（{self.comfy_workflow_dir} 为空，用兜底工作流）"
        return (
            f"🎨 Comfy豆包通用生图 v{PLUGIN_VERSION}\n"
            f"生成后端：{BACKEND_ZH.get(self.backend, self.backend)}"
            f"（本地 {self.comfy_url}，{self.comfy_width}×{self.comfy_height}，"
            f"步数 {self.comfy_steps}）\n"
            f"内容审查：{mod_line}｜识图模型：{mod_prov}\n"
            f"人格参考图：{persona_line}\n"
            f"云端模型：{self.model}\n"
            f"尺寸：{self.size}（该模型支持：{size_scope}）｜ "
            f"格式：{self.output_format} ｜ 水印：{'开' if self.watermark else '关'} ｜ "
            f"数量：{self.count}\n"
            f"组图（一次多张）：{'支持' if self.supports_group else '当前模型不支持'}"
            f"（注意：参考图张数 + 生成张数 ≤ {TOTAL_IMAGE_LIMIT}）\n"
            f"联网搜索：{('已开启' if self.web_search else '未开启')}"
            f"（{'本模型支持' if self.supports_web_search else '本模型不支持'}）｜ "
            f"提示词优化：{self.optimize_prompt_mode}"
            f"{'' if self.supports_fast_prompt else '（fast 仅 5.0 pro / 4.0 支持）'}\n"
            f"API Key：{key_status}\n"
            f"参考图：{ref_status}（本模型上限 {self.caps_max_refs} 张）\n"
            f"附带提示词：{prompt_echo}\n"
            f"提示词层：{'已开启' if self.prompt_guide_enabled else '已关闭'}"
            "（system_prompt 注入「生图前自查」｜工具说明含固定模板与 3 个示例｜"
            "本地 Qwen 保留 <imageN> 槽位，云端自动翻成自然语言）\n"
            f"本地工作流：{wf_line}\n"
            f"{self._cost_line()}\n"
            f"省钱护栏：每人每天 {self.daily_limit if self.daily_limit else '不限'} 张｜"
            f"同图缓存{cache_txt}｜今日已生成 {self._daily_total()} 张\n\n"
            "用法：\n"
            "· /画图 <提示词> 　→ 自动：带图就图生图，不带图就文生图\n"
            "· /图生图 [提示词] → 强制图生图（需先发图或引用一张带图的消息）\n"
            "· /文生图 <提示词> → 强制文生图（即使消息里带了图也忽略）\n"
            "· /生图 <提示词> 　→ 同 /画图\n"
            "· /画图诊断 　　　→ 零成本自检：后端 / 本地 ComfyUI / 审查 / 云端账户\n"
            "· /画图审查 　　　→ 看审查配置，以及哪些模型能当识图模型\n"
            "· /画图参考 　　　→ 看人格参考图目录现状（谁有图、谁没图）\n"
            "· /画图帮助 　　　→ 本说明\n\n"
            "参考图的两种来源（AI 自己决定用哪个、或都不用）：\n"
            "【A】人格参考图 —— 由用户提前放好：\n"
            "    把图片丢进插件目录的 refs/ 文件夹，\n"
            "    文件名（不含扩展名）= AI 人格名，例如人格叫「橙橘」就放 橙橘.png。\n"
            "    名字对不上时，在配置 reference_alias_map 里写一行「人格名=文件名」。\n"
            "    这一路走的是本地「参考图工作流」；万一它报错，插件会自动改用纯文本工作流\n"
            "    重新生成，并在图片下方说明本次没用上参考图。\n"
            "【B】消息附图 —— 图片和指令发在同一条消息、引用一条带图的消息、\n"
            "    或把图片链接直接写进提示词。\n"
            "两种都不给也能画，直接 /画图 <提示词> 即可。\n\n"
            "本地工作流（AI 自己挑）：\n"
            f"· 插件扫描 {self.comfy_workflow_dir} 下的所有 *.json（必须 API 格式），\n"
            "  把「名字 + 用途」清单交给 AI 按需求自己挑，不用你每次指定。\n"
            "· 同名 .txt 就是它的说明：首行写【名称】，余下写什么时候该用它。\n"
            "  加一套新工作流只要丢两个文件进目录，不改配置也不改代码。\n"
            "· 想固定用某套：把 comfyui_workflow_select 关掉，再设 comfyui_default_workflow。\n\n"
            "内容审查：\n"
            "· 生成前先让识图模型判断提示词；生成后再判断出图的图片，命中 R18 直接拦截不发。\n"
            "· 识图模型在配置 moderation_provider_id 里填 Provider ID（发 /画图审查 可看列表）。\n"
            "· 拿不准时默认按安全处理；审查模型调不通时默认放行（都能在配置里改）。\n\n"
            "💡 省钱要点（官方价）：\n"
            "· 5.0 flash = 0.12 元/张 —— 全系最低，且输入参考图免费（无免费额度）；\n"
            "· 4.0 = 0.20 元/张，另送 200 张免费额度 —— 想白嫖额度选它；\n"
            "· 5.0 / 5.0-lite = 0.22，4.5 = 0.25（4.5 也送 200 张）；\n"
            "· pro 是唯一按像素分档的：1K/1.5K 收 0.30，2K 及以上收 0.60（翻倍！）\n"
            "  且 pro **没有免费额度**。同样的提示词，换 flash 直接省 4/5。\n"
            "· flash / 4.0 / 4.5 / lite 的单价与尺寸无关，开 2K 不会加钱；\n"
            "· 组图按张数计费（count=2 就是两倍钱）；审核未通过的图不计费。\n\n"
            "提示：AI 也可以在对话里自己调用生图（说「画一只猫」试试）。"
        )
