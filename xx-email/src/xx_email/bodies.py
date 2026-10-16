"""Plain-text bodies for the thread view (stdlib only, no GTK, no WebKit).

WebKit is outside the allowed stack, so bodies render as sanitized
plain text: the ``text/plain`` part wins when present, otherwise the
``text/html`` part converts through ``html.parser``. No remote fetch
happens during render at all, which makes the remote-image policy
stronger than Android's default (tracking pixels stay dead; an
explicit per-message control may be added separately).
"""

from __future__ import annotations

import base64
import html as html_module
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

MAX_BODY_CHARS = 200_000

_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style).*?</\1\s*>")
_EVENT_ATTR_RE = re.compile(
    r"(?i)\s+on\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_JAVASCRIPT_RE = re.compile(r"(?i)javascript:")
_IMG_RE = re.compile(r"(?is)<img[^>]*>")
_TRACKING_IMG_RE = re.compile(
    r"(?is)<img[^>]*(?:\bwidth\s*=\s*[\"']?1[\"']?|\bheight\s*=\s*[\"']?1[\"']?"
    r"|\bdisplay\s*:\s*none|tracking|beacon|open[_-]?pixel)[^>]*>")
_LINK_RE = re.compile(r"(?is)<link[^>]*>")


def has_remote_images(html_text: str | None) -> bool:
    """True when the HTML references any image (remote-content gate)."""
    if not html_text:
        return False
    return _IMG_RE.search(html_text) is not None


def sanitize_html(html_text: str,
                  allow_remote_images: bool = False) -> str:
    """Strip script vectors from email HTML (best-effort regex pass).

    Ports ``sanitizeHtml`` in the Android ``ui/components`` layer: drops
    ``script``/``style`` elements, inline event handlers and
    ``javascript:`` URLs, plus every ``<img>`` unless remote images are
    explicitly allowed. Tracking pixels (1 px images and beacon tags)
    are always dropped, even when allowed: consent covers visible
    images, never read receipts. A plain-string pass, NOT a general
    hardening layer — never reuse it with a WebView.
    """
    out = _SCRIPT_STYLE_RE.sub("", html_text or "")
    out = _EVENT_ATTR_RE.sub("", out)
    out = _JAVASCRIPT_RE.sub("", out)
    out = _LINK_RE.sub("", out)
    if not allow_remote_images:
        out = _IMG_RE.sub("", out)
    else:
        out = _TRACKING_IMG_RE.sub("", out)
    return out


class _TextExtractor(HTMLParser):
    """HTML to readable text: block tags break lines, scripts vanish."""

    BLOCK_TAGS = frozenset([
        "p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2",
        "h3", "h4", "h5", "h6", "blockquote", "pre", "hr",
    ])
    SKIP_TAGS = frozenset(["script", "style", "head", "noscript"])

    def __init__(self, show_images: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._show_images = show_images

    def _image_marker(self, attrs: list) -> None:
        if not self._show_images or self._skip_depth:
            return
        alt = ""
        for key, value in attrs or []:
            if key.lower() == "alt" and value:
                alt = f" {value.strip()}"[:80]
                break
        self._chunks.append(f"\n[image{alt}]\n")

    def handle_starttag(self, tag: str, attrs: list) -> None:
        name = tag.lower()
        if name in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if name == "img":
            self._image_marker(attrs)
            return
        if name == "br" or name == "hr":
            self._chunks.append("\n")
        elif name in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag.lower() in ("br", "hr"):
            self._chunks.append("\n")
        elif tag.lower() == "img":
            self._image_marker(attrs)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if name in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in
                 raw.splitlines()]
        cleaned: list[str] = []
        blank = False
        for line in lines:
            if line:
                cleaned.append(line)
                blank = False
            elif not blank:
                cleaned.append("")
                blank = True
        return html_module.unescape("\n".join(cleaned).strip())


def html_to_text(html_text: str, show_images: bool = False) -> str:
    """Convert an HTML body to readable plain text (never fetches).

    Images render as ``[image]`` markers only when the viewer allowed
    remote content for the message; otherwise they stay invisible.
    """
    if not html_text or not html_text.strip():
        return ""
    parser = _TextExtractor(show_images=show_images)
    try:
        parser.feed(html_text[:MAX_BODY_CHARS])
        parser.close()
    except Exception:
        return html_module.unescape(
            re.sub(r"<[^>]+>", "", html_text))[:MAX_BODY_CHARS].strip()
    return parser.text()[:MAX_BODY_CHARS]


def render_body(body_html: str | None, body_plain: str | None,
                snippet: str = "",
                allow_remote_images: bool = False) -> str:
    """Pick the display text for one message.

    Decoded ``text/plain`` wins when present; otherwise the HTML part
    converts (scripts stripped — they could never run here anyway);
    the sync-time snippet is the last resort. Remote content is never
    fetched either way.
    """
    if body_plain is not None and body_plain.strip():
        return body_plain.strip()[:MAX_BODY_CHARS]
    if body_html is not None and body_html.strip():
        converted = html_to_text(
            sanitize_html(body_html,
                          allow_remote_images=allow_remote_images),
            show_images=allow_remote_images)
        if converted:
            return converted
    return (snippet or "").strip()


def decode_base64url(data: str | None) -> str | None:
    """Decode a Gmail part body (url-safe first, MIME fallback)."""
    raw = decode_base64url_bytes(data)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def decode_base64url_bytes(data: str | None) -> bytes | None:
    """Decode a Gmail part body to raw bytes (attachments stay binary)."""
    if not data:
        return None
    try:
        return base64.urlsafe_b64decode(data)
    except ValueError:
        pass
    try:
        return base64.b64decode(data)
    except ValueError:
        return None


@dataclass
class AttachmentMeta:
    message_id: str
    attachment_id: str
    filename: str
    mime_type: str = ""
    size: int = 0


def decode_attachments(raw_json: str | None) -> list[AttachmentMeta]:
    """Parse stored attachment metadata; corrupt rows yield no chips."""
    if not raw_json or not raw_json.strip():
        return []
    try:
        payload = json.loads(raw_json)
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []
    found = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        message_id = entry.get("messageId", "")
        attachment_id = entry.get("attachmentId", "")
        filename = entry.get("filename", "")
        if not message_id or not attachment_id or not filename:
            continue
        size = entry.get("size", 0)
        found.append(AttachmentMeta(
            message_id=str(message_id),
            attachment_id=str(attachment_id),
            filename=str(filename),
            mime_type=str(entry.get("mimeType", "") or ""),
            size=size if isinstance(size, int) else 0,
        ))
    return found
