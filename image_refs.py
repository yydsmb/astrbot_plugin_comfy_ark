"""图片引用相关的纯标准库工具。

本模块**不依赖 astrbot**，因此可以脱离框架直接单测。
主要职责：
  - 从文本里抽取可能的图片链接；
  - 把任意来源的参考图（http 链接 / 本地路径 / file:// / base64 / data URI）
    统一成火山方舟接口能接受的 `data:image/xxx;base64,...`；
  - 通过魔数（magic bytes）嗅探真实图片格式，避免把非图片内容传给接口；
  - 日志脱敏（绝不把 base64 写进日志）。
"""

from __future__ import annotations

import base64
import os
import re

# ----------------------------------------------------------------------
# 魔数 → MIME
# ----------------------------------------------------------------------
_MAGIC_TABLE: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

# 从自由文本里找 http(s) 链接：遇到空白、中文标点、引号等即截断
_URL_RE = re.compile(
    r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\"'<>()\[\]{}，。；！？、]+",
    re.IGNORECASE,
)

# 看起来像 base64 的字符串（纯字母数字 + / =，允许多行空白）
_B64_CHARS_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")

# 常见图片扩展名（仅用于日志/提示，不作为判定依据）
_IMAGE_EXT_RE = re.compile(
    r"\.(?:png|jpe?g|webp|bmp|gif|tiff?|heic|avif)(?:\?|#|$)",
    re.IGNORECASE,
)

MAX_PROMPT_CHARS = 4000  # 火山方舟 prompt 上限


# ----------------------------------------------------------------------
# 基础判定
# ----------------------------------------------------------------------
def is_http_url(value: str) -> bool:
    """是否是可直接抓取的 http(s) 链接。"""
    if not value or not isinstance(value, str):
        return False
    v = value.strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def looks_like_image_url(value: str) -> bool:
    """链接是否「看起来像」图片（扩展名启发式，仅用于降低误判）。"""
    return bool(value) and bool(_IMAGE_EXT_RE.search(value))


def looks_like_base64(value: str) -> bool:
    """是否是裸 base64 数据（没有 data URI 前缀的那种）。"""
    if not value or not isinstance(value, str):
        return False
    body = value.strip()
    if len(body) < 64:
        return False
    sample = body[:512]
    if not _B64_CHARS_RE.match(sample):
        return False
    # 去掉空白后长度必须接近 4 的倍数
    compact = re.sub(r"\s+", "", body)
    return len(compact) % 4 in (0, 2, 3)


def sniff_image_mime(data: bytes) -> str | None:
    """通过魔数判断图片 MIME；无法识别时返回 None。"""
    if not data or len(data) < 4:
        return None
    for magic, mime in _MAGIC_TABLE:
        if data.startswith(magic):
            return mime
    # WEBP: RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # BMP（2 字节魔数太弱，额外要求长度合理且保留字段为 0）
    if len(data) >= 14 and data[:2] == b"BM" and data[6:10] == b"\x00\x00\x00\x00":
        return "image/bmp"
    # HEIF/HEIC: ....ftyp<brand>
    if len(data) >= 12 and data[4:8] == b"ftyp":
        if data[8:12] in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
            return "image/heic"
    # AVIF
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return None


def bytes_to_data_uri(data: bytes) -> str | None:
    """把图片字节转成 data URI；识别不出图片格式时返回 None。"""
    mime = sniff_image_mime(data)
    if not mime:
        return None
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def readable_size(num_bytes: float) -> str:
    """人类可读的体积。"""
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}GB"


# ----------------------------------------------------------------------
# 文本处理
# ----------------------------------------------------------------------
def extract_image_urls_from_text(text: str) -> list[str]:
    """从文本里抽取 http(s) 链接（去重、保序）。

    不做「是不是图片」的判断——交给上层下载后按魔数校验，
    这样即使链接没有扩展名也能正确处理。
    """
    if not text:
        return []
    found: list[str] = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?，。；：！？")
        if url and url not in found:
            found.append(url)
    return found


def strip_command_prefix(text: str, commands: tuple[str, ...] = ()) -> str:
    """去掉文本开头的指令词（`/画图`、`画图`、`/生图` …）及其后的空白。"""
    if not text:
        return ""
    out = text.strip()
    # 反复剥离，处理「/图生图 参考」这类
    for _ in range(3):
        changed = False
        if out.startswith("/"):
            out = out[1:].lstrip()
            changed = True
        for cmd in commands:
            if not cmd:
                continue
            if out.startswith(cmd):
                rest = out[len(cmd) :]
                # 只有紧跟空白/结束才算命中，避免「画图师」被误剥
                if not rest or rest[0].isspace():
                    out = rest.lstrip()
                    changed = True
                    break
        if not changed:
            break
    return out.strip()


def truncate_prompt(prompt: str, limit: int = MAX_PROMPT_CHARS) -> str:
    """按接口上限截断提示词。"""
    if not prompt:
        return ""
    p = prompt.strip()
    if len(p) <= limit:
        return p
    return p[:limit]


def sanitize_ref_for_log(ref: str) -> str:
    """把参考图引用转成可安全写日志的短字符串（绝不输出 base64）。"""
    if not ref:
        return "<empty>"
    if not isinstance(ref, str):
        return f"<{type(ref).__name__}>"
    r = ref.strip()
    if r.startswith("data:"):
        head = r.split(",", 1)[0]
        return f"{head},...<{readable_size(len(r))}>"
    if r.startswith("base64://"):
        return f"base64://...<{readable_size(len(r))}>"
    if looks_like_base64(r):
        return f"<base64 {readable_size(len(r))}>"
    if len(r) > 100:
        return r[:100] + "..."
    return r


def local_path_from_ref(ref: str) -> str | None:
    """把 `file://` 或本地路径引用转成存在的绝对路径；不存在返回 None。"""
    if not ref:
        return None
    r = ref.strip()

    if r.lower().startswith("file://"):
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        parsed = urlparse(r)
        path = url2pathname(unquote(parsed.path))
        # Windows 上 file:///C:/x 会解析成 /C:/x
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        elif os.name == "nt" and parsed.netloc and parsed.netloc != "localhost":
            path = f"//{parsed.netloc}{path}"
        return path if os.path.isfile(path) else None

    # 直接就是本地路径
    try:
        if os.path.isfile(r):
            return r
    except (OSError, ValueError):
        pass
    return None
