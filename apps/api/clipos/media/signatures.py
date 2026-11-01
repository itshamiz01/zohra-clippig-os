from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath

from clipos.core.enums import MediaKind
from clipos.media.errors import UploadValidationError

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")
_SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".m4a",
    ".mp3",
    ".wav",
    ".webm",
    ".mkv",
    ".ogg",
    ".oga",
    ".opus",
    ".flac",
    ".aac",
}


@dataclass(frozen=True, slots=True)
class DetectedMedia:
    content_type: str
    safe_extension: str
    kind_hint: MediaKind
    family: str


def sanitize_display_filename(filename: str | None) -> str:
    candidate = (filename or "unnamed-media").replace("\\", "/").split("/")[-1]
    candidate = "".join(
        character for character in candidate if unicodedata.category(character) not in {"Cc", "Cf"}
    )
    candidate = _CONTROL_CHARS.sub("", candidate).strip().strip(".")
    if not candidate:
        candidate = "unnamed-media"
    return candidate[:240]


def _validate_extension(filename: str | None, allowed: set[str]) -> str:
    suffix = PurePath(sanitize_display_filename(filename)).suffix.lower()
    if suffix and suffix not in _SUPPORTED_EXTENSIONS:
        raise UploadValidationError(
            "unsupported_extension", "filename extension is not an allowed media type"
        )
    if suffix and suffix not in allowed:
        raise UploadValidationError(
            "extension_signature_mismatch",
            "filename extension does not match the detected file signature",
        )
    return suffix


def _validate_declared_type(declared: str | None, allowed_prefixes: tuple[str, ...]) -> None:
    if not declared or declared.lower() in {"application/octet-stream", "binary/octet-stream"}:
        return
    normalized = declared.split(";", 1)[0].strip().lower()
    if not any(normalized.startswith(prefix) for prefix in allowed_prefixes):
        raise UploadValidationError(
            "mime_signature_mismatch",
            "declared content type does not match the detected media signature",
        )


def detect_media_signature(
    header: bytes, *, filename: str | None, declared_content_type: str | None
) -> DetectedMedia:
    if len(header) < 4:
        raise UploadValidationError("empty_or_truncated", "media file is empty or truncated")

    # ISO Base Media File Format: MP4, MOV, M4A and related variants.
    if len(header) >= 12 and header[4:8] == b"ftyp":
        allowed = {".mp4", ".mov", ".m4v", ".m4a"}
        suffix = _validate_extension(filename, allowed)
        _validate_declared_type(
            declared_content_type,
            ("video/mp4", "video/quicktime", "audio/mp4", "application/mp4"),
        )
        kind = MediaKind.AUDIO if suffix == ".m4a" else MediaKind.UNKNOWN
        content_type = "audio/mp4" if kind == MediaKind.AUDIO else "video/mp4"
        return DetectedMedia(content_type, suffix or ".mp4", kind, "iso_bmff")

    if header.startswith(b"\x1aE\xdf\xa3"):
        allowed = {".webm", ".mkv"}
        suffix = _validate_extension(filename, allowed)
        _validate_declared_type(
            declared_content_type, ("video/webm", "audio/webm", "video/x-matroska")
        )
        return DetectedMedia(
            "video/webm" if suffix != ".mkv" else "video/x-matroska",
            suffix or ".webm",
            MediaKind.UNKNOWN,
            "ebml",
        )

    if header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WAVE":
        suffix = _validate_extension(filename, {".wav"})
        _validate_declared_type(declared_content_type, ("audio/wav", "audio/x-wav", "audio/wave"))
        return DetectedMedia("audio/wav", suffix or ".wav", MediaKind.AUDIO, "wav")

    if header.startswith(b"fLaC"):
        suffix = _validate_extension(filename, {".flac"})
        _validate_declared_type(declared_content_type, ("audio/flac", "audio/x-flac"))
        return DetectedMedia("audio/flac", suffix or ".flac", MediaKind.AUDIO, "flac")

    if header.startswith(b"OggS"):
        allowed = {".ogg", ".oga", ".opus"}
        suffix = _validate_extension(filename, allowed)
        _validate_declared_type(
            declared_content_type, ("audio/ogg", "video/ogg", "application/ogg")
        )
        return DetectedMedia("audio/ogg", suffix or ".ogg", MediaKind.UNKNOWN, "ogg")

    # ADTS uses a 12-bit sync word and must be checked before the broader MPEG frame sync.
    if len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xF6) == 0xF0:
        suffix = _validate_extension(filename, {".aac"})
        _validate_declared_type(declared_content_type, ("audio/aac", "audio/x-aac"))
        return DetectedMedia("audio/aac", suffix or ".aac", MediaKind.AUDIO, "aac")

    if header.startswith(b"ID3") or (
        len(header) >= 2
        and header[0] == 0xFF
        and (header[1] & 0xE0) == 0xE0
        and (header[1] & 0x06) != 0
    ):
        suffix = _validate_extension(filename, {".mp3"})
        _validate_declared_type(declared_content_type, ("audio/mpeg", "audio/mp3"))
        return DetectedMedia("audio/mpeg", suffix or ".mp3", MediaKind.AUDIO, "mp3")

    raise UploadValidationError(
        "unsupported_signature", "file signature is not a supported audio or video format"
    )
