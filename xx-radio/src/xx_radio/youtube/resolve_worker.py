"""Turn song ids into fetchable audio locations, in a child process.

Runs as ``python3 -m xx_radio.youtube.resolve_worker``: JSON over stdin,
JSON over stdout, then exits. The parent (resolver.py) owns timeouts and
killing; this process never downloads anything itself, it only asks yt-dlp
for the address and headers the player (through the stream proxy) fetches.

Stdin: {"items": [{"id": str, "low_data": bool}], "cookie_file": str|None,
  "player_clients": [str]|None, "plugin_dirs": [str],
  "js_runtimes": {name: path}|None, "extractor_args": {..}|None,
  "remote_components": [str]|None, "signed_in": bool, "helper_ready": bool}

Stdout: {"results": [{"id": str, "ok": true, "uri": str, "headers": {},
  "mime": str, "expires_at": float, "size_bytes": int|None,
  "gain_db": float|None} | {"id": str, "ok": false, "kind": str,
  "error": str, "retry_after_s": float|None}]}
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from typing import Any

from xx_music.engine import ErrorKind

#: Opus in WebM plays without extra codecs; AAC is the fallback.
FORMAT_FULL = "bestaudio[acodec=opus]/bestaudio/best[acodec!=none]"
#: Metered networks with Data saver on.
FORMAT_LOW = ("bestaudio[acodec=opus][abr<=72]/bestaudio[abr<=96]/bestaudio")
#: Used when the address carries no expiry stamp of its own.
DEFAULT_EXPIRY_S = 4 * 3600
#: Playback stalls wait this long before the player moves on.
RATE_LIMIT_RETRY_S = 60.0

_EXT_MIME = {
    "webm": "audio/webm",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "wav": "audio/wav",
}


def audio_format(low_data: bool) -> str:
    return FORMAT_LOW if low_data else FORMAT_FULL


def parse_expiry(url: str, now: float | None = None,
                 default_s: float = DEFAULT_EXPIRY_S) -> float:
    """Unix time the address stops working: its ``expire=`` stamp, else
    ``now`` + 4 h. 0 and garbage fall back to the default."""
    moment = time.time() if now is None else now
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query)
        stamp = float(query.get("expire", [""])[0])
    except (ValueError, TypeError):
        stamp = 0.0
    if stamp > 0:
        return stamp
    return moment + default_s


def classify_failure(message: str, *, signed_in: bool = False,
                     helper_ready: bool = True) -> ErrorKind:
    """Map yt-dlp's error text to an engine kind (PORT_SPEC 4.2).

    ``signed_in``/``helper_ready`` disambiguate the rows that depend on
    them; matching is case-insensitive and order matters.
    """
    text = (message or "").casefold()
    if any(key in text for key in (
            "private video", "video unavailable", "video is unavailable",
            "removed by")):
        return ErrorKind.NOT_FOUND
    if any(key in text for key in ("members-only", "join this channel")):
        return ErrorKind.NOT_AVAILABLE
    if any(key in text for key in ("confirm your age", "age-restricted",
                                   "age restricted")):
        return ErrorKind.NOT_AVAILABLE if signed_in else ErrorKind.AUTH_REQUIRED
    if any(key in text for key in (
            "sign in to confirm you're not a bot",
            "sign in to confirm you are not a bot",
            "http error 403", "http 403")):
        return ErrorKind.RATE_LIMITED
    if any(key in text for key in ("requested format is not available",
                                   "only images are available",
                                   "no audio")):
        return ErrorKind.SOURCE_CHANGED if helper_ready \
            else ErrorKind.HELPER_MISSING
    if any(key in text for key in ("unable to extract", "unable to download",
                                   "nsig", "signature", "player response",
                                   "json did not")):
        return ErrorKind.SOURCE_CHANGED
    if any(key in text for key in ("please sign in", "please log in",
                                   "login required", "sign in to access",
                                   "use --cookies")):
        return ErrorKind.AUTH_REQUIRED
    if "timeout" in text or "timed out" in text:
        return ErrorKind.TIMEOUT
    if any(key in text for key in (
            "urlopen error", "name or service not known",
            "temporary failure in name resolution", "failed to resolve",
            "getaddrinfo", "nodename nor servname", "network is unreachable",
            "no route to host", "connection refused", "connection reset",
            "connection aborted", "broken pipe", "remote end closed",
            "eof occurred", "network is down", "no internet")):
        return ErrorKind.OFFLINE
    return ErrorKind.OTHER


def build_ytdl_opts(audio_format_text: str, *,
                    cookie_file: str | None = None,
                    player_clients: list[str] | None = None,
                    plugin_dirs: list[str] | None = None,
                    js_runtimes: dict[str, str] | None = None,
                    extractor_args: dict[str, Any] | None = None,
                    remote_components: list[str] | None = None,
                    ) -> dict[str, Any]:
    """Pure helper: the YoutubeDL options for one format group."""
    opts: dict[str, Any] = {
        "format": audio_format_text,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if plugin_dirs:
        opts["plugin_dirs"] = list(plugin_dirs)
    if js_runtimes:
        opts["js_runtimes"] = dict(js_runtimes)
    if remote_components:
        opts["remote_components"] = list(remote_components)
    merged: dict[str, Any] = {}
    if extractor_args:
        merged = {key: dict(value) if isinstance(value, dict) else value
                  for key, value in extractor_args.items()}
    if player_clients:
        youtube_args = merged.get("youtube")
        if not isinstance(youtube_args, dict):
            youtube_args = {}
        youtube_args["player_client"] = list(player_clients)
        merged["youtube"] = youtube_args
    if merged:
        opts["extractor_args"] = merged
    return opts


def _failure_text(exc: BaseException) -> str:
    parts = [f"{type(exc).__name__}: {exc}"]
    seen = {id(exc)}
    cause: BaseException | None = exc
    while True:
        cause = cause.__cause__ if cause.__cause__ is not None \
            else cause.__context__
        if cause is None or id(cause) in seen:
            break
        seen.add(id(cause))
        parts.append(f"{type(cause).__name__}: {cause}")
    return " / ".join(part for part in parts if part.strip(": "))


def _info_to_result(video_id: str, info: dict[str, Any]) -> dict[str, Any]:
    entries = info.get("entries")
    if isinstance(entries, (list, tuple)):
        picked = [entry for entry in entries if entry]
        info = picked[0] if picked else {}
    elif entries is not None and not isinstance(entries, dict):
        try:
            info = next(iter(entries))
        except (StopIteration, TypeError):
            info = {}
    uri = info.get("url") or ""
    if not uri:
        raise ValueError("requested format is not available")
    headers = {str(key): str(value)
               for key, value in dict(info.get("http_headers") or {}).items()}
    ext = str(info.get("audio_ext") or info.get("ext") or "").lower()
    mime = str(info.get("mime") or _EXT_MIME.get(ext, ""))
    size = info.get("filesize")
    if size is None:
        size = info.get("filesize_approx")
    try:
        size_bytes = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    gain = info.get("loudness")
    try:
        gain_db = float(gain) if gain is not None else None
    except (TypeError, ValueError):
        gain_db = None
    return {
        "id": video_id,
        "ok": True,
        "uri": uri,
        "headers": headers,
        "mime": mime,
        "expires_at": parse_expiry(uri),
        "size_bytes": size_bytes,
        "gain_db": gain_db,
    }


def _resolve_group(items: list[dict[str, Any]], ytdl_opts: dict[str, Any],
                   signed_in: bool, helper_ready: bool) -> list[dict]:
    import yt_dlp  # noqa: PLC0415  (fake in tests, real on the phone)

    results = []
    with yt_dlp.YoutubeDL(ytdl_opts) as downloader:
        for item in items:
            video_id = str(item["id"])
            try:
                info = downloader.extract_info(
                    f"https://music.youtube.com/watch?v={video_id}",
                    download=False)
                results.append(_info_to_result(video_id, info or {}))
            except Exception as exc:  # noqa: BLE001  (mapped, never raised)
                text = _failure_text(exc)
                kind = classify_failure(text, signed_in=signed_in,
                                        helper_ready=helper_ready)
                results.append({
                    "id": video_id,
                    "ok": False,
                    "kind": kind.value,
                    "error": text[:500],
                    "retry_after_s": (RATE_LIMIT_RETRY_S
                                      if kind == ErrorKind.RATE_LIMITED
                                      else None),
                })
    return results


def resolve_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve one worker request dict into a response dict."""
    raw_items = payload.get("items") or []
    items = [{"id": str(item.get("id", "")),
              "low_data": bool(item.get("low_data", False))}
             for item in raw_items if str(item.get("id", ""))]
    signed_in = bool(payload.get("signed_in", False))
    helper_ready = bool(payload.get("helper_ready", True))
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(audio_format(item["low_data"]), []).append(item)
    results: list[dict[str, Any]] = []
    for format_text, group in groups.items():
        opts = build_ytdl_opts(
            format_text,
            cookie_file=payload.get("cookie_file"),
            player_clients=payload.get("player_clients"),
            plugin_dirs=payload.get("plugin_dirs"),
            js_runtimes=payload.get("js_runtimes"),
            extractor_args=payload.get("extractor_args"),
            remote_components=payload.get("remote_components"))
        results.extend(_resolve_group(group, opts, signed_in, helper_ready))
    return {"results": results}


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(f"resolve_worker: bad request: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict) or not isinstance(
            payload.get("items"), list):
        print("resolve_worker: request needs an 'items' list",
              file=sys.stderr)
        return 2
    try:
        response = resolve_payload(payload)
    except Exception as exc:  # noqa: BLE001  (import/setup failure)
        print(f"resolve_worker: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
