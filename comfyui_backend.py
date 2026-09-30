"""ComfyUI 后端的纯逻辑层 —— 零 astrbot 依赖，可脱离框架单测。

职责边界：
  · 地址归一化与各端点 URL 拼接（**必须对中文文件名做 URL 编码**，见 view_endpoint）
  · 从「API 格式」工作流里自动识别「该改哪个节点的哪个字段」
  · 按参数改写工作流
  · 从 /history 记录里取出输出图
  · 把 ComfyUI 的报错翻成人话

为什么是「自动识别」而不是「配节点 ID」：
  用户手里的工作流是通过 ComfyUI 前端导出的，节点 ID 会随改动漂移。
  实测两个工作流里 TextEncode 的 ID 就不同（一个是 4、一个是 6），
  SaveImage 一个在 8、一个在 21。所以按**连线关系**反推语义，
  而不是把 ID 写死进配置 —— 换工作流不用改配置。

识别算法（以实际工作流为准）：
  1) 找采样器：class_type == KSampler 优先，其次名字里带 Sampler 且带
     positive/negative/latent_image 输入的节点。
  2) 采样器的 inputs.positive / inputs.negative 指向的节点 = 文本编码节点。
  3) 文本字段：按 TEXT_FIELDS 的顺序取第一个存在的键
     （实测 t2i 与参考图工作流都是 prompt / negative_prompt）。
  4) 采样器的 inputs.latent_image 指向的节点决定尺寸来源：
       · EmptyLatentImage 之类带 width/height 的 → size_mode="empty"
       · 否则（实测参考图工作流里是 TextEncode 的 latent 输出，按参考图尺寸出图）
         → size_mode="encode"，此时不注入宽高。
  5) LoadImage 节点 = 参考图挂载点；它到文本编码节点的连线键（实测
     "images.image_1"）也一并记下来。
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------
DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_POLL_INTERVAL = 3.0
DEFAULT_TIMEOUT = 600.0

PROMPT_PATH = "/prompt"
HISTORY_PATH = "/history"
VIEW_PATH = "/view"
UPLOAD_PATH = "/upload/image"
OBJECT_INFO_PATH = "/object_info"
QUEUE_PATH = "/queue"
SYSTEM_STATS_PATH = "/system_stats"
INTERRUPT_PATH = "/interrupt"

# 文本编码节点里「正面提示词」可能出现的字段名（按优先级）
TEXT_FIELDS = (
    "prompt",
    "text",
    "text_positive",
    "positive",
    "string",
    "wildcard_text",
    "prompt_text",
)
# 「负面提示词」可能出现的字段名（按优先级）
NEG_FIELDS = (
    "negative_prompt",
    "negative",
    "text_negative",
    "neg_text",
)
# 尺寸节点里宽高字段
WIDTH_FIELDS = ("width", "W", "w")
HEIGHT_FIELDS = ("height", "H", "h")
# 采样器里种子 / 步数
SEED_FIELDS = ("seed", "noise_seed")
STEPS_FIELDS = ("steps",)

# 参考图连线的键前缀（ComfyUI 的 COMFY_AUTOGROW_V3 就长这样：images.image_1）
REF_LINK_RE = re.compile(r"^images\.image_\d+$")

# 能当「尺寸节点」看的 class_type 特征
EMPTY_LATENT_HINTS = ("EmptyLatentImage", "EmptyLatent", "EmptySD3LatentImage")
LOAD_IMAGE_HINTS = ("LoadImage",)

# 主机名校验（挡 `!!!!!!!!` 这种：urlparse 见 netloc 非空就照收）
_HOST_RE = re.compile(
    r"^(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9._\-]*[A-Za-z0-9])?)"
    r"(?::\d{1,5})?$"
)


# ----------------------------------------------------------------------
# 地址
# ----------------------------------------------------------------------
def normalize_comfy_url(raw: Any) -> str:
    """把用户写的 ComfyUI 地址整理成 `scheme://host[:port]`（保留子路径）。

    容忍这些写法：`127.0.0.1:8188`、`localhost:8188/`、`http://host:8188/prompt`、
    末尾多余的斜杠、前后空格。主机名不合法时回落默认值 —— 否则
    `urlparse("!!!!!!!!")` 会因为 netloc 非空而原样吐出一个假地址。
    """
    text = str(raw or "").strip().strip("\"'").strip()
    if not text:
        return DEFAULT_COMFY_URL
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlparse(text)
    except ValueError:
        return DEFAULT_COMFY_URL

    netloc = parts.netloc or ""
    if not netloc:
        return DEFAULT_COMFY_URL
    if not _HOST_RE.match(netloc):
        return DEFAULT_COMFY_URL

    scheme = (parts.scheme or "http").lower()
    if scheme not in ("http", "https"):
        scheme = "http"

    path = (parts.path or "").rstrip("/")
    for suffix in (PROMPT_PATH, HISTORY_PATH, VIEW_PATH, OBJECT_INFO_PATH, UPLOAD_PATH):
        if path.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            break
    return f"{scheme}://{netloc}{path}"


def is_local_address(url: str) -> bool:
    """判断是否本机 / 内网地址（用于绕过系统代理，也用于上传参考图的安全边界）。"""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in ("localhost", "localhost.localdomain", "ip6-localhost"):
        return True
    if host.endswith(".local") or host.endswith(".localhost"):
        return True
    if (
        host == "::1"
        or host.startswith("fe80:")
        or host.startswith("fc")
        or host.startswith("fd")
    ):
        return True
    if re.match(r"^127\.", host) or re.match(r"^10\.", host):
        return True
    if re.match(r"^192\.168\.", host):
        return True
    if re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host):
        return True
    if re.match(r"^169\.254\.", host):
        return True
    # 裸主机名（无点）也当内网，局域网机器名通常长这样
    if "." not in host and ":" not in host:
        return True
    return False


def prompt_endpoint(base: str) -> str:
    return normalize_comfy_url(base) + PROMPT_PATH


def history_endpoint(base: str, prompt_id: str) -> str:
    return f"{normalize_comfy_url(base)}{HISTORY_PATH}/{quote(str(prompt_id), safe='')}"


def view_endpoint(
    base: str,
    filename: str,
    subfolder: str = "",
    file_type: str = "output",
    *,
    as_preview: bool = False,
) -> str:
    """拼 /view URL。

    ⚠️ 必须 URL 编码：工作流的 filename_prefix 常是中文（实测 `Qwen_2.1-文生图`），
    直接把这个文件名塞进 URL 会让 http 客户端抛
    `UnicodeEncodeError: 'ascii' codec can't encode characters`。
    """
    params = [
        ("filename", str(filename or "")),
        ("subfolder", str(subfolder or "")),
        ("type", str(file_type or "output")),
    ]
    if as_preview:
        params.append(("preview", "true"))
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params)
    return f"{normalize_comfy_url(base)}{VIEW_PATH}?{query}"


def upload_endpoint(base: str) -> str:
    return normalize_comfy_url(base) + UPLOAD_PATH


def object_info_endpoint(base: str, node: str = "") -> str:
    base = normalize_comfy_url(base) + OBJECT_INFO_PATH
    if node:
        base += "/" + quote(str(node), safe="")
    return base


def queue_endpoint(base: str) -> str:
    return normalize_comfy_url(base) + QUEUE_PATH


def system_stats_endpoint(base: str) -> str:
    return normalize_comfy_url(base) + SYSTEM_STATS_PATH


def interrupt_endpoint(base: str) -> str:
    return normalize_comfy_url(base) + INTERRUPT_PATH


# ----------------------------------------------------------------------
# 工作流识别
# ----------------------------------------------------------------------
@dataclass
class WorkflowSpec:
    """从工作流里反推出来的「语义 → 节点/字段」映射。"""

    sampler_node: str = ""
    positive_node: str = ""
    positive_field: str = ""
    negative_node: str = ""
    negative_field: str = ""
    latent_node: str = ""
    size_mode: str = (
        "empty"  # empty = 用 EmptyLatentImage 出画布；encode = 按参考图尺寸
    )
    width_field: str = ""
    height_field: str = ""
    seed_field: str = ""
    steps_field: str = ""
    load_image_node: str = ""
    load_image_field: str = ""
    ref_link_key: str = ""  # 例如 images.image_1；为空表示这个工作流不吃参考图
    resolution_field: str = ""
    batch_field: str = ""
    save_node: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def supports_reference(self) -> bool:
        """能塞参考图 = 有 LoadImage 节点，且它已连到文本编码节点上。

        实测 t2i 工作流（tu_API.json）里 TextEncodeQwenImage21 的 images.* 是空的
        （min=0，允许 0 张），即那个工作流结构上就不支持参考图 —— 这正是
        「参考图工作流失败要回退文生图」这条需求的根因。
        """
        return bool(self.load_image_node and self.ref_link_key)

    def describe(self) -> str:
        bits = [
            f"采样器={self.sampler_node}",
            f"文本节点={self.positive_node}({self.positive_field})",
        ]
        if self.negative_field:
            bits.append(f"负面字段={self.negative_node}({self.negative_field})")
        if self.size_mode == "empty":
            bits.append(
                f"尺寸节点={self.latent_node}({self.width_field}x{self.height_field})"
            )
        else:
            bits.append("尺寸=按参考图")
        bits.append(
            f"参考图={'节点' + self.load_image_node if self.load_image_node else '不支持'}"
        )
        return "｜".join(bits)


def _node(wf: dict[str, Any], node_id: Any) -> dict[str, Any] | None:
    node = wf.get(str(node_id))
    return node if isinstance(node, dict) else None


def _inputs(node: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    ins = node.get("inputs")
    return ins if isinstance(ins, dict) else {}


def _linked_node(node: dict[str, Any] | None, key: str) -> str:
    """取 `inputs[key]` 这类连线的上游节点 ID（不是连线就返回空串）。"""
    value = _inputs(node).get(key)
    if isinstance(value, (list, tuple)) and value:
        head = value[0]
        if isinstance(head, (str, int)):
            return str(head)
    return ""


def _first_field(inputs: dict[str, Any], candidates) -> str:
    for name in candidates:
        if name in inputs:
            return name
    return ""


def _class_type(node: dict[str, Any] | None) -> str:
    if not isinstance(node, dict):
        return ""
    return str(node.get("class_type") or "")


def _is_empty_latent(node: dict[str, Any] | None) -> bool:
    ct = _class_type(node)
    if any(h in ct for h in EMPTY_LATENT_HINTS):
        return True
    ins = _inputs(node)
    return bool(ins) and ("width" in ins and "height" in ins) and "images" not in ins


def find_sampler(wf: dict[str, Any]) -> str:
    """找采样节点。KSampler 精确命中优先；否则按「带采样器特征输入」打分。"""
    best: tuple[int, str] = (-1, "")
    for node_id in sorted(wf.keys(), key=lambda x: str(x)):
        node = _node(wf, node_id)
        ct = _class_type(node)
        if not ct:
            continue
        ins = _inputs(node)
        score = 0
        if ct == "KSampler":
            score = 100
        elif "Sampler" in ct:
            score = 50
        else:
            continue
        for key in ("positive", "negative", "latent_image", "model"):
            if key in ins:
                score += 3
        for key in ("seed", "noise_seed", "steps"):
            if key in ins:
                score += 2
        if score > best[0]:
            best = (score, str(node_id))
    return best[1]


def find_load_image(wf: dict[str, Any]) -> str:
    for node_id in sorted(wf.keys(), key=lambda x: str(x)):
        ct = _class_type(_node(wf, node_id))
        if any(h in ct for h in LOAD_IMAGE_HINTS):
            return str(node_id)
    return ""


def find_save_node(wf: dict[str, Any]) -> str:
    for node_id in sorted(wf.keys(), key=lambda x: str(x)):
        if "SaveImage" in _class_type(_node(wf, node_id)):
            return str(node_id)
    return ""


def detect_workflow(wf: dict[str, Any]) -> WorkflowSpec:
    """从 API 格式工作流反推语义映射。认不出来时把原因写进 spec.notes。"""
    spec = WorkflowSpec()
    if not isinstance(wf, dict) or not wf:
        spec.notes.append("工作流为空或不是 API 格式")
        return spec

    sampler_id = find_sampler(wf)
    if not sampler_id:
        spec.notes.append("找不到采样器节点（KSampler），无法确定改哪里")
        return spec
    sampler = _node(wf, sampler_id)
    spec.sampler_node = sampler_id

    pos_id = _linked_node(sampler, "positive")
    if not pos_id:
        spec.notes.append("采样器的 positive 输入没有连线")
    spec.positive_node = pos_id
    pos_ins = _inputs(_node(wf, pos_id))
    spec.positive_field = _first_field(pos_ins, TEXT_FIELDS)
    if pos_id and not spec.positive_field:
        spec.notes.append(f"节点 {pos_id} 上找不到可写的提示词字段")

    neg_id = _linked_node(sampler, "negative")
    spec.negative_node = neg_id
    neg_ins = _inputs(_node(wf, neg_id))
    if neg_id:
        spec.negative_field = _first_field(neg_ins, NEG_FIELDS)
        if not spec.negative_field and neg_id == pos_id:
            # 正负面共用同一个 TextEncode 节点（实测两个工作流都这样）
            spec.negative_field = ""

    sampler_ins = _inputs(sampler)
    spec.seed_field = _first_field(sampler_ins, SEED_FIELDS)
    spec.steps_field = _first_field(sampler_ins, STEPS_FIELDS)

    latent_id = _linked_node(sampler, "latent_image")
    spec.latent_node = latent_id
    latent_node = _node(wf, latent_id)
    if _is_empty_latent(latent_node):
        spec.size_mode = "empty"
        lat_ins = _inputs(latent_node)
        spec.width_field = _first_field(lat_ins, WIDTH_FIELDS)
        spec.height_field = _first_field(lat_ins, HEIGHT_FIELDS)
        spec.batch_field = "batch_size" if "batch_size" in lat_ins else ""
    elif latent_id:
        # 实测：参考图工作流把这根线接到 TextEncode 的 latent 输出，
        # 也就是「按第一张参考图的尺寸出画布」，此时不能注入宽高。
        spec.size_mode = "encode"

    # 分辨率字段（Qwen Image 2.1 用它控制参考图被缩放到多大，0 = 保持原尺寸）
    for candidate in (spec.positive_node, spec.negative_node, spec.latent_node):
        ins = _inputs(_node(wf, candidate))
        if "resolution" in ins:
            spec.resolution_field = "resolution"
            break

    spec.load_image_node = find_load_image(wf)
    if spec.load_image_node:
        spec.load_image_field = _first_field(
            _inputs(_node(wf, spec.load_image_node)), ("image", "image_path", "path")
        )
        spec.ref_link_key = _find_ref_link_key(
            wf, (spec.positive_node, spec.negative_node)
        )
        if not spec.ref_link_key:
            spec.notes.append(
                f"有 LoadImage 节点 {spec.load_image_node}，但它没有连到文本编码节点，"
                "该工作流无法吃参考图"
            )

    spec.save_node = find_save_node(wf)
    return spec


def _find_ref_link_key(wf: dict[str, Any], node_ids: tuple[str, ...]) -> str:
    """在文本编码节点的输入里找 `images.image_N` 这种参考图连线键。"""
    for node_id in node_ids:
        if not node_id:
            continue
        for key in _inputs(_node(wf, node_id)):
            if REF_LINK_RE.match(str(key)):
                return str(key)
    return ""


def workflow_kind(wf: dict[str, Any], spec: WorkflowSpec | None = None) -> str:
    """`ref` = 能吃参考图；`t2i` = 只能文生图。"""
    spec = spec or detect_workflow(wf)
    return "ref" if spec.supports_reference else "t2i"


def workflow_node_types(wf: dict[str, Any]) -> list[str]:
    """列出工作流用到的所有 class_type（用于「本机缺哪些节点」自检）。"""
    out: list[str] = []
    for node_id in wf:
        ct = _class_type(_node(wf, node_id))
        if ct and ct not in out:
            out.append(ct)
    return out


# ----------------------------------------------------------------------
# 改写工作流
# ----------------------------------------------------------------------
def apply_params(
    wf: dict[str, Any],
    spec: WorkflowSpec,
    *,
    prompt: str | None = None,
    negative_prompt: str | None = None,
    seed: int | None = None,
    steps: int | None = None,
    width: int | None = None,
    height: int | None = None,
    batch_size: int | None = None,
    resolution: int | None = None,
    ref_image_name: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """按语义映射改写一份工作流副本，返回 (新工作流, 说明列表)。

    不修改入参 —— 调用方拿到的原工作流保持干净，方便重试时复用。
    """
    new = copy.deepcopy(wf)
    notes: list[str] = []

    if prompt is not None and spec.positive_node and spec.positive_field:
        _inputs(new.get(spec.positive_node))[spec.positive_field] = str(prompt)

    if negative_prompt is not None and spec.negative_node and spec.negative_field:
        _inputs(new.get(spec.negative_node))[spec.negative_field] = str(negative_prompt)

    sampler = _inputs(new.get(spec.sampler_node)) if spec.sampler_node else {}
    if seed is not None and spec.seed_field:
        sampler[spec.seed_field] = int(seed)
    if steps is not None and spec.steps_field:
        sampler[spec.steps_field] = int(steps)

    if spec.size_mode == "empty" and spec.latent_node:
        lat = _inputs(new.get(spec.latent_node))
        if width is not None and spec.width_field:
            lat[spec.width_field] = int(width)
        if height is not None and spec.height_field:
            lat[spec.height_field] = int(height)
        if batch_size is not None and spec.batch_field:
            lat[spec.batch_field] = int(batch_size)

    if resolution is not None and spec.resolution_field:
        for node_id in (spec.positive_node, spec.negative_node, spec.latent_node):
            ins = _inputs(new.get(node_id)) if node_id else {}
            if spec.resolution_field in ins:
                ins[spec.resolution_field] = int(resolution)
                break

    if ref_image_name:
        if not spec.supports_reference:
            notes.append("该工作流不支持参考图（缺少已连线的 LoadImage 节点）")
        elif spec.load_image_field:
            _inputs(new.get(spec.load_image_node))[spec.load_image_field] = str(
                ref_image_name
            )
        else:
            notes.append(f"节点 {spec.load_image_node} 上找不到可写的图片字段")

    return new, notes


def randomize_seeds(wf: dict[str, Any], value: int) -> dict[str, Any]:
    """把工作流里所有 seed / noise_seed 统一换成同一个值（种子复现用）。"""
    new = copy.deepcopy(wf)

    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: (int(value) if k in ("seed", "noise_seed") else walk(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        return obj

    return walk(new)


# ----------------------------------------------------------------------
# 结果解析
# ----------------------------------------------------------------------
@dataclass
class OutputImage:
    filename: str
    subfolder: str = ""
    file_type: str = "output"
    node_id: str = ""
    node_type: str = ""

    def endpoint(self, base: str, *, as_preview: bool = False) -> str:
        return view_endpoint(
            base, self.filename, self.subfolder, self.file_type, as_preview=as_preview
        )


# 优先取图的输出节点类型（实测 SaveImage / SaveImageAdvanced 都在里面）
IMAGE_HOLDING_TYPES = (
    "SaveImage",
    "PreviewImage",
    "SaveImageWebsocket",
    "SaveImageAdvanced",
)


def extract_images(history_record: dict[str, Any] | None) -> list[OutputImage]:
    """从 /history/{id} 的**单条记录**里取出所有输出图。

    记录形如：{"outputs": {"8": {"images": [{filename, subfolder, type}]}}, ...}
    按节点类型优先级排序（SaveImage 系优先），保证拿到的是成品图而不是预览图。
    """
    if not isinstance(history_record, dict):
        return []
    outputs = history_record.get("outputs")
    if not isinstance(outputs, dict):
        return []

    found: list[tuple[int, OutputImage]] = []
    for node_id, payload in outputs.items():
        if not isinstance(payload, dict):
            continue
        images = payload.get("images")
        if not isinstance(images, list):
            continue
        node_type = str(payload.get("class_type") or "")
        prio = 0
        if any(t in node_type for t in IMAGE_HOLDING_TYPES):
            prio = 2
        elif node_type:
            prio = 1
        for item in images:
            if not isinstance(item, dict) or not item.get("filename"):
                continue
            found.append(
                (
                    prio,
                    OutputImage(
                        filename=str(item.get("filename")),
                        subfolder=str(item.get("subfolder") or ""),
                        file_type=str(item.get("type") or "output"),
                        node_id=str(node_id),
                        node_type=node_type,
                    ),
                )
            )
    found.sort(key=lambda x: -x[0])
    return [img for _, img in found]


def history_status(history_record: dict[str, Any] | None) -> tuple[str, str]:
    """返回 (状态, 说明)。状态取值：completed / error / running / unknown。

    ComfyUI 的 /history 只在任务**结束后**才出现记录，所以拿不到记录时是 running。
    """
    if not isinstance(history_record, dict):
        return "running", ""
    status = history_record.get("status")
    if not isinstance(status, dict):
        return "unknown", ""
    if status.get("status_str") == "error":
        return "error", describe_execution_errors(status.get("messages"))
    if status.get("completed"):
        return "completed", ""
    return "running", ""


def describe_execution_errors(messages: Any) -> str:
    """把 status.messages 里的事件流压成一句人话（含节点异常原文）。"""
    if not isinstance(messages, list):
        return "执行失败（无详细信息）"
    lines: list[str] = []
    for item in messages:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        kind = str(item[0] or "")
        data = item[1]
        if kind == "execution_error" and isinstance(data, dict):
            node_type = data.get("node_type") or "?"
            node_id = data.get("node_id") or "?"
            exc_type = data.get("exception_type") or "Exception"
            msg = str(data.get("exception_message") or "").strip()
            lines.append(f"节点 {node_id}（{node_type}）抛 {exc_type}：{msg[:300]}")
        elif kind == "execution_interrupted":
            lines.append("任务被中断")
    return "；".join(lines) if lines else "执行失败（无详细信息）"


# ----------------------------------------------------------------------
# 报错翻译
# ----------------------------------------------------------------------
_NODE_ERROR_HINTS = {
    "return_type_mismatch": "连线类型对不上（工作流被改动后没重连）",
    "missing_node_type": "本机没装这个自定义节点",
    "value_not_in_list": "节点参数取值不在允许范围内（多半是模型文件名不对）",
    "invalid_image": "参考图无法读取",
    "required_input_missing": "必填输入为空",
    "prompt_outputs_failed_validation": "工作流校验没通过",
}


def explain_http_error(status: int, body: str, base_url: str = "") -> str:
    """把 ComfyUI 的 HTTP 报错翻成「人话 + 原文」。"""
    base = normalize_comfy_url(base_url) if base_url else ""
    text = str(body or "")[:1200]

    if status == 404:
        return (
            f"❌ ComfyUI 返回 404（{text[:200]}）。"
            f"通常是地址写错 —— 请确认 {base or '配置里的地址'} 是本机 ComfyUI 的地址"
            "（默认 8188 端口），且没有被反向代理改写路径。"
        )
    if status == 500:
        return f"❌ ComfyUI 内部错误 500：{text[:300]}。请看 ComfyUI 控制台里的堆栈。"
    if status != 400:
        return f"❌ ComfyUI 返回 HTTP {status}：{text[:300]}"

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return f"❌ ComfyUI 拒绝了这个工作流（400）：{text[:300]}"

    node_errors = data.get("node_errors") if isinstance(data, dict) else None
    if isinstance(node_errors, dict) and node_errors:
        lines: list[str] = []
        for node_id, info in list(node_errors.items())[:6]:
            node_type = ""
            errors = []
            if isinstance(info, dict):
                node_type = str(info.get("class_type") or "")
                errors = info.get("errors") or []
            for err in errors[:3]:
                if not isinstance(err, dict):
                    continue
                etype = str(err.get("type") or "")
                emsg = str(err.get("message") or "").strip()
                hint = _NODE_ERROR_HINTS.get(etype, "")
                piece = f"节点 {node_id}"
                if node_type:
                    piece += f"（{node_type}）"
                piece += f"：{emsg[:200]}"
                if hint:
                    piece += f" → {hint}"
                extra = err.get("extra_info")
                if isinstance(extra, dict) and extra.get("node_type"):
                    piece += f"（缺的节点类型：{extra['node_type']}）"
                lines.append(piece)
        head = "❌ ComfyUI 拒绝了这个工作流，逐节点报错如下："
        return head + "\n" + "\n".join(lines)

    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        etype = str(err.get("type") or "")
        emsg = str(err.get("message") or "").strip()
        detail = str(err.get("details") or "").strip()
        hint = _NODE_ERROR_HINTS.get(etype, "")
        out = f"❌ ComfyUI 报错：{emsg[:300]}"
        if detail:
            out += f"（{detail[:200]}）"
        if hint:
            out += f" → {hint}"
        return out
    return f"❌ ComfyUI 拒绝了这个工作流（400）：{text[:300]}"


def explain_exception(exc: BaseException, base_url: str = "") -> str:
    """把 aiohttp / 连接层异常翻成人话。"""
    base = normalize_comfy_url(base_url) if base_url else ""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()

    if (
        "cannot connect" in low
        or "connection refused" in low
        or "connect call failed" in low
        or "refused" in low
        # 类型名比字符串更靠得住：aiohttp 的 ClientConnectorError、
        # 以及被包装过的 ConnectionRefusedError / ServerDisconnectedError 都走这里
        or name
        in (
            "ClientConnectorError",
            "ClientConnectionError",
            "ClientOSError",
            "ConnectionError",
            "ConnectionRefusedError",
            "ConnectionResetError",
            "ServerDisconnectedError",
        )
    ):
        return (
            f"❌ 连不上 ComfyUI（{base or '配置的地址'}）。"
            "请确认 ComfyUI 正在运行、端口没变，并且它的启动参数允许本机访问。"
        )
    if "timeout" in low or name in ("TimeoutError", "ServerTimeoutError"):
        return f"⏱️ 等待 ComfyUI 超时（{base}）。可能是显存不够、模型在换入换出，或队列里排了别的任务。"
    if "clientpayloaderror" in low or "content length" in low:
        return f"❌ 从 ComfyUI 读数据时中断：{text[:200]}"
    if name == "InvalidURL":
        return f"❌ ComfyUI 地址不合法：{base}"
    return f"❌ ComfyUI 调用异常（{name}）：{text[:250]}"


# ----------------------------------------------------------------------
# 能力探测（/object_info、/system_stats）
# ----------------------------------------------------------------------
def missing_node_types(wf: dict[str, Any], object_info: dict[str, Any]) -> list[str]:
    """工作流用到、但本机 /object_info 里没有的节点类型。"""
    available = set(object_info.keys()) if isinstance(object_info, dict) else set()
    return [t for t in workflow_node_types(wf) if t and t not in available]


def summarize_system_stats(stats: Any) -> str:
    """把 /system_stats 压成一行，用来自检。"""
    if not isinstance(stats, dict):
        return "未取到 ComfyUI 运行信息"
    system = stats.get("system") if isinstance(stats.get("system"), dict) else {}
    devices = stats.get("devices") if isinstance(stats.get("devices"), list) else []

    ver = system.get("comfyui_version") or "?"
    py = str(system.get("python_version") or "").split(" ")[0]
    ram_total = system.get("ram_total")
    ram_free = system.get("ram_free")

    def gib(v: Any) -> str:
        try:
            return f"{float(v) / (1024**3):.1f}GB"
        except (TypeError, ValueError):
            return "?"

    parts = [f"ComfyUI {ver}"]
    if py:
        parts.append(f"Python {py}")
    if ram_total:
        parts.append(f"内存 {gib(ram_free)}/{gib(ram_total)} 空闲")
    for dev in devices[:2]:
        if not isinstance(dev, dict):
            continue
        name = str(dev.get("name") or dev.get("type") or "?").strip()
        vt = dev.get("vram_total")
        vf = dev.get("vram_free")
        seg = name
        if vt:
            seg += f" 显存 {gib(vf)}/{gib(vt)} 空闲"
        parts.append(seg)
    return "｜".join(parts)


def summarize_queue(queue_payload: Any) -> str:
    """把 /queue 压成一行（正在跑几个、排队几个）。"""
    if not isinstance(queue_payload, dict):
        return "未知"
    running = queue_payload.get("queue_running")
    pending = queue_payload.get("queue_pending")
    r = len(running) if isinstance(running, list) else 0
    p = len(pending) if isinstance(pending, list) else 0
    return f"运行中 {r} 个，排队 {p} 个"


def parse_object_info(payload: Any) -> dict[str, Any]:
    """ComfyUI 有时会包一层（{"object_info": {...}}），统一扒平。"""
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("object_info")
    if isinstance(inner, dict):
        return inner
    return payload


def enum_options(object_info: dict[str, Any], node: str, field_name: str) -> list[str]:
    """取某个节点某个输入的可选值列表（用于「模型文件在不在」自检）。"""
    node_info = object_info.get(node) if isinstance(object_info, dict) else None
    if not isinstance(node_info, dict):
        return []
    spec = node_info.get("input")
    if not isinstance(spec, dict):
        return []
    for group in ("required", "optional"):
        fields = spec.get(group)
        if isinstance(fields, dict) and field_name in fields:
            value = fields[field_name]
            if isinstance(value, list) and value and isinstance(value[0], list):
                return [str(x) for x in value[0]]
    return []


# ----------------------------------------------------------------------
# 其它小工具
# ----------------------------------------------------------------------
def clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, num))


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def comfy_steps_default(caps: dict[str, Any]) -> int:
    """Qwen Image 2.1 工作流的官方推荐步数是 25（cfg=1，euler + simple）。"""
    return int(caps.get("steps") or 25)
