from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import resource
import shutil
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clipos.config import Settings
from clipos.core.enums import MediaKind
from clipos.media.errors import (
    MediaPipelineError,
    MediaProcessCancelledError,
    MediaProcessTimeoutError,
    MediaToolUnavailableError,
)

logger = logging.getLogger(__name__)

_SUPPORTED_VIDEO_CODECS = {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "prores"}
_SUPPORTED_AUDIO_CODECS = {
    "aac",
    "mp3",
    "opus",
    "vorbis",
    "flac",
    "alac",
    "pcm_s16le",
    "pcm_s24le",
    "pcm_s32le",
    "pcm_f32le",
}


@dataclass(frozen=True, slots=True)
class ToolStatus:
    available: bool
    ffmpeg_version: str | None
    ffprobe_version: str | None
    error_category: str | None = None
    render_ready: bool = False
    capabilities: tuple[str, ...] = ()
    render_limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderSpec:
    source: Path
    destination: Path
    start_seconds: float
    duration_seconds: float
    width: int
    height: int
    frame_rate: float
    crop_strategy: str
    caption_file: Path | None
    caption_style: str
    safe_bottom_fraction: float
    branding_file: Path | None
    branding_position: str
    branding_opacity: float
    branding_font_size: int
    normalize_audio: bool
    target_lufs: float
    true_peak_db: float
    audio_sample_rate: int
    audio_channels: int
    x264_preset: str
    crf: int
    max_output_bytes: int


@dataclass(frozen=True, slots=True)
class ProbeResult:
    format_name: str
    duration_seconds: float
    bit_rate: int | None
    has_video: bool
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None
    frame_rate: float | None
    audio_channels: int | None
    audio_sample_rate: int | None
    media_kind: MediaKind
    probe_version: str
    raw: dict[str, Any]


class _OutputLimitExceeded(RuntimeError):
    pass


def _resource_limiter(memory_mb: int, cpu_seconds: int, file_size_bytes: int) -> Callable[[], None]:
    def apply() -> None:
        memory_bytes = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_size_bytes, file_size_bytes))

    return apply


async def _read_limited(reader: asyncio.StreamReader, limit: int) -> bytes:
    output = bytearray()
    while True:
        chunk = await reader.read(min(65536, limit + 1 - len(output)))
        if not chunk:
            return bytes(output)
        output.extend(chunk)
        if len(output) > limit:
            raise _OutputLimitExceeded


def _safe_diagnostic(raw: bytes, media_path: Path | None = None) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\x00", "")
    if media_path:
        text = text.replace(str(media_path), "[media]")
    return " ".join(text.split())[-1000:]


class MediaTooling:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ffmpeg = shutil.which(settings.ffmpeg_path) or settings.ffmpeg_path
        self.ffprobe = shutil.which(settings.ffprobe_path) or settings.ffprobe_path
        self.status = ToolStatus(False, None, None, "not_checked")

    async def _run(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        operation: str,
        media_path: Path | None = None,
        output_file_limit: int | None = None,
    ) -> tuple[bytes, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                preexec_fn=_resource_limiter(
                    self.settings.media_memory_limit_mb,
                    self.settings.media_cpu_limit_seconds,
                    output_file_limit or self.settings.max_upload_bytes,
                ),
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise MediaToolUnavailableError(Path(args[0]).name) from exc
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(
            _read_limited(process.stdout, self.settings.media_subprocess_output_bytes)
        )
        stderr_task = asyncio.create_task(
            _read_limited(process.stderr, self.settings.media_subprocess_output_bytes)
        )
        try:
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task, process.wait()),
                timeout=timeout_seconds,
            )
        except asyncio.CancelledError:
            await self._terminate(process)
            await self._cancel_readers(stdout_task, stderr_task)
            raise
        except TimeoutError as exc:
            await self._terminate(process)
            await self._cancel_readers(stdout_task, stderr_task)
            raise MediaProcessTimeoutError(operation) from exc
        except _OutputLimitExceeded as exc:
            await self._terminate(process)
            await self._cancel_readers(stdout_task, stderr_task)
            raise MediaPipelineError(
                "media_tool_output_limit",
                f"{operation} produced excessive diagnostic output",
                retryable=False,
            ) from exc
        if process.returncode != 0:
            diagnostic = _safe_diagnostic(stderr, media_path)
            if diagnostic:
                logger.warning("%s failed with bounded diagnostic: %s", operation, diagnostic)
            raise MediaPipelineError(
                f"{operation}_failed",
                f"{operation} could not process the supplied media",
                retryable=False,
            )
        return stdout, stderr

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()

    @staticmethod
    async def _cancel_readers(*tasks: asyncio.Task[Any]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def check(self) -> ToolStatus:
        try:
            ffmpeg_out, _ = await self._run(
                [self.ffmpeg, "-version"],
                timeout_seconds=5,
                operation="ffmpeg_check",
            )
            ffprobe_out, _ = await self._run(
                [self.ffprobe, "-version"],
                timeout_seconds=5,
                operation="ffprobe_check",
            )
            filters_out, _ = await self._run(
                [self.ffmpeg, "-hide_banner", "-filters"],
                timeout_seconds=10,
                operation="ffmpeg_filter_check",
            )
            encoders_out, _ = await self._run(
                [self.ffmpeg, "-hide_banner", "-encoders"],
                timeout_seconds=10,
                operation="ffmpeg_encoder_check",
            )
            ffmpeg_version = ffmpeg_out.decode(errors="replace").splitlines()[0][:160]
            ffprobe_version = ffprobe_out.decode(errors="replace").splitlines()[0][:160]
            filters_text = filters_out.decode("utf-8", errors="replace")
            encoders_text = encoders_out.decode("utf-8", errors="replace")
            capabilities = {
                name
                for name in ("scale", "crop", "pad", "fps", "loudnorm", "subtitles", "drawtext")
                if _ffmpeg_listing_contains(filters_text, name)
            }
            if _ffmpeg_listing_contains(encoders_text, "libx264"):
                capabilities.add("libx264")
            if _ffmpeg_listing_contains(encoders_text, "aac"):
                capabilities.add("aac")
            required = {"scale", "crop", "pad", "fps", "loudnorm", "libx264", "aac"}
            missing = sorted(required - capabilities)
            limitations: list[str] = []
            if missing:
                limitations.append("missing required render capabilities: " + ", ".join(missing))
            if "subtitles" not in capabilities:
                limitations.append(
                    "subtitle burn-in unavailable; sidecar captions remain available"
                )
            if "drawtext" not in capabilities:
                limitations.append("tenant text branding unavailable")
            limitations.append(
                "face detection/tracking unavailable; smart requests use center crop"
            )
            self.status = ToolStatus(
                True,
                ffmpeg_version,
                ffprobe_version,
                render_ready=not missing,
                capabilities=tuple(sorted(capabilities)),
                render_limitations=tuple(limitations),
            )
        except MediaPipelineError as exc:
            self.status = ToolStatus(False, None, None, exc.category)
        return self.status

    async def probe(self, path: Path) -> ProbeResult:
        if not self.status.available:
            await self.check()
        if not self.status.available:
            raise MediaToolUnavailableError("ffprobe/ffmpeg")
        stdout, _ = await self._run(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_entries",
                (
                    "format=format_name,duration,bit_rate:"
                    "stream=codec_type,codec_name,width,height,avg_frame_rate,"
                    "r_frame_rate,channels,sample_rate,duration"
                ),
                "-of",
                "json",
                str(path),
            ],
            timeout_seconds=self.settings.media_probe_timeout_seconds,
            operation="ffprobe",
            media_path=path,
        )
        try:
            raw = json.loads(stdout)
            streams = raw.get("streams", [])
            format_data = raw.get("format", {})
            if not isinstance(streams, list) or not isinstance(format_data, dict):
                raise TypeError
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise MediaPipelineError(
                "ffprobe_invalid_output", "ffprobe returned invalid metadata", retryable=False
            ) from exc

        video = next((item for item in streams if item.get("codec_type") == "video"), None)
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        if not video and not audio:
            raise MediaPipelineError(
                "no_media_streams", "media contains neither a video nor an audio stream"
            )

        duration = self._duration(format_data, streams)
        if duration <= 0 or not math.isfinite(duration):
            raise MediaPipelineError("invalid_duration", "media duration is missing or invalid")
        if duration > self.settings.max_media_duration_seconds:
            raise MediaPipelineError(
                "duration_limit", "media duration exceeds the configured limit"
            )

        video_codec = str(video.get("codec_name")) if video and video.get("codec_name") else None
        audio_codec = str(audio.get("codec_name")) if audio and audio.get("codec_name") else None
        if video_codec and video_codec not in _SUPPORTED_VIDEO_CODECS:
            raise MediaPipelineError(
                "unsupported_video_codec",
                f"video codec {video_codec} is not supported",
                needs_review=True,
            )
        if audio_codec and audio_codec not in _SUPPORTED_AUDIO_CODECS:
            raise MediaPipelineError(
                "unsupported_audio_codec",
                f"audio codec {audio_codec} is not supported",
                needs_review=True,
            )

        width = _optional_int(video.get("width")) if video else None
        height = _optional_int(video.get("height")) if video else None
        if width and width > self.settings.max_media_dimension:
            raise MediaPipelineError("dimension_limit", "video width exceeds configured limit")
        if height and height > self.settings.max_media_dimension:
            raise MediaPipelineError("dimension_limit", "video height exceeds configured limit")

        frame_rate = _parse_rate(
            (video.get("avg_frame_rate") or video.get("r_frame_rate")) if video else None
        )
        media_kind = MediaKind.VIDEO if video else MediaKind.AUDIO
        return ProbeResult(
            format_name=str(format_data.get("format_name") or "unknown")[:160],
            duration_seconds=duration,
            bit_rate=_optional_int(format_data.get("bit_rate")),
            has_video=bool(video),
            has_audio=bool(audio),
            video_codec=video_codec,
            audio_codec=audio_codec,
            width=width,
            height=height,
            frame_rate=frame_rate,
            audio_channels=_optional_int(audio.get("channels")) if audio else None,
            audio_sample_rate=_optional_int(audio.get("sample_rate")) if audio else None,
            media_kind=media_kind,
            probe_version=self.status.ffprobe_version or "unknown",
            raw={"format": format_data, "streams": streams},
        )

    async def extract_audio(self, source: Path, destination: Path, duration: float) -> None:
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True, mode=0o700)
        await asyncio.to_thread(destination.unlink, missing_ok=True)
        expected_max = min(
            self.settings.max_derived_audio_bytes,
            max(1024 * 1024, int(duration * 32000) + 1024 * 1024),
        )
        await self._run(
            [
                self.ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                str(destination),
            ],
            timeout_seconds=self.settings.media_extract_timeout_seconds,
            operation="audio_extraction",
            media_path=source,
            output_file_limit=expected_max,
        )
        try:
            size = (await asyncio.to_thread(destination.stat)).st_size
        except FileNotFoundError as exc:
            raise MediaPipelineError(
                "audio_output_missing", "audio extraction produced no output"
            ) from exc
        if size <= 44 or size > expected_max:
            await asyncio.to_thread(destination.unlink, missing_ok=True)
            raise MediaPipelineError(
                "audio_output_invalid", "audio extraction produced an invalid output size"
            )

    async def render_clip(
        self,
        spec: RenderSpec,
        *,
        progress_callback: Callable[[float], Awaitable[None]] | None = None,
        cancellation_requested: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        if not self.status.available:
            await self.check()
        if not self.status.available or not self.status.render_ready:
            raise MediaToolUnavailableError("FFmpeg rendering capabilities")
        capabilities = set(self.status.capabilities)
        if spec.caption_file is not None and "subtitles" not in capabilities:
            raise MediaPipelineError(
                "subtitle_filter_unavailable",
                "FFmpeg subtitle burn-in capability is unavailable",
                needs_review=True,
            )
        if spec.branding_file is not None and "drawtext" not in capabilities:
            raise MediaPipelineError(
                "branding_filter_unavailable",
                "FFmpeg tenant-branding capability is unavailable",
                needs_review=True,
            )
        await asyncio.to_thread(
            spec.destination.parent.mkdir, parents=True, exist_ok=True, mode=0o700
        )
        await asyncio.to_thread(spec.destination.unlink, missing_ok=True)

        if spec.crop_strategy == "fit_pad":
            video_filters = [
                (
                    f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=decrease,"
                    f"pad={spec.width}:{spec.height}:(ow-iw)/2:(oh-ih)/2:color=black"
                )
            ]
        else:
            video_filters = [
                (
                    f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=increase,"
                    f"crop={spec.width}:{spec.height}"
                )
            ]
        video_filters.extend(["setsar=1", f"fps={spec.frame_rate:g}"])
        if spec.caption_file is not None:
            caption_path = _escape_filter_path(spec.caption_file)
            font_size = max(18, min(64, round(spec.height * 0.035)))
            margin_v = max(18, round(spec.height * spec.safe_bottom_fraction))
            outline = 3 if spec.caption_style == "high_contrast_bottom" else 2
            force_style = (
                f"Alignment=2,FontSize={font_size},PrimaryColour=&H00FFFFFF,"
                f"OutlineColour=&H00101010,BorderStyle=1,Outline={outline},"
                f"Shadow=0,MarginV={margin_v}"
            )
            video_filters.append(f"subtitles=filename='{caption_path}':force_style='{force_style}'")
        if spec.branding_file is not None:
            brand_path = _escape_filter_path(spec.branding_file)
            margin_x = max(16, round(spec.width * 0.04))
            margin_y = max(16, round(spec.height * 0.04))
            positions = {
                "top_left": (str(margin_x), str(margin_y)),
                "top_right": (f"w-tw-{margin_x}", str(margin_y)),
                "bottom_left": (str(margin_x), f"h-th-{margin_y}"),
                "bottom_right": (f"w-tw-{margin_x}", f"h-th-{margin_y}"),
            }
            x_value, y_value = positions.get(spec.branding_position, positions["top_right"])
            video_filters.append(
                "drawtext="
                f"textfile='{brand_path}':reload=0:"
                f"x={x_value}:y={y_value}:fontsize={spec.branding_font_size}:"
                f"fontcolor=white@{spec.branding_opacity:.3f}:"
                "box=1:boxcolor=black@0.35:boxborderw=10"
            )

        args = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-v",
            "warning",
            "-ss",
            f"{spec.start_seconds:.3f}",
            "-t",
            f"{spec.duration_seconds:.3f}",
            "-i",
            str(spec.source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            ",".join(video_filters),
        ]
        if spec.normalize_audio:
            args.extend(
                [
                    "-af",
                    (
                        f"loudnorm=I={spec.target_lufs:g}:TP={spec.true_peak_db:g}:"
                        "LRA=11:linear=true"
                    ),
                ]
            )
        args.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                spec.x264_preset,
                "-crf",
                str(spec.crf),
                "-pix_fmt",
                "yuv420p",
                "-profile:v",
                "high",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                str(spec.audio_sample_rate),
                "-ac",
                str(spec.audio_channels),
                "-movflags",
                "+faststart",
                "-max_muxing_queue_size",
                "1024",
                "-threads",
                "2",
                "-progress",
                "pipe:1",
                "-nostats",
                "-n",
                str(spec.destination),
            ]
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                preexec_fn=_resource_limiter(
                    self.settings.media_memory_limit_mb,
                    self.settings.media_cpu_limit_seconds,
                    spec.max_output_bytes,
                ),
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise MediaToolUnavailableError("ffmpeg") from exc
        assert process.stdout is not None
        assert process.stderr is not None
        progress_task = asyncio.create_task(
            self._read_render_progress(
                process.stdout,
                duration_seconds=spec.duration_seconds,
                callback=progress_callback,
            )
        )
        stderr_task = asyncio.create_task(
            _read_limited(process.stderr, self.settings.media_subprocess_output_bytes)
        )
        wait_task = asyncio.create_task(process.wait())
        cancellation_task = asyncio.create_task(
            self._monitor_render_cancellation(process, cancellation_requested)
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(progress_task, stderr_task, wait_task, cancellation_task),
                timeout=self.settings.render_timeout_seconds,
            )
        except asyncio.CancelledError:
            await self._terminate(process)
            await self._cancel_readers(progress_task, stderr_task, wait_task, cancellation_task)
            raise
        except MediaProcessCancelledError:
            await self._terminate(process)
            await self._cancel_readers(progress_task, stderr_task, wait_task, cancellation_task)
            raise
        except TimeoutError as exc:
            await self._terminate(process)
            await self._cancel_readers(progress_task, stderr_task, wait_task, cancellation_task)
            raise MediaProcessTimeoutError("render") from exc
        except _OutputLimitExceeded as exc:
            await self._terminate(process)
            await self._cancel_readers(progress_task, stderr_task, wait_task, cancellation_task)
            raise MediaPipelineError(
                "media_tool_output_limit",
                "render produced excessive diagnostic output",
                retryable=False,
            ) from exc
        if process.returncode != 0:
            diagnostic = _safe_diagnostic(stderr_task.result(), spec.source)
            if diagnostic:
                logger.warning("render failed with bounded diagnostic: %s", diagnostic)
            await asyncio.to_thread(spec.destination.unlink, missing_ok=True)
            raise MediaPipelineError(
                "render_process_failed",
                "FFmpeg could not render the validated editing plan",
                retryable=False,
            )
        try:
            size = (await asyncio.to_thread(spec.destination.stat)).st_size
        except FileNotFoundError as exc:
            raise MediaPipelineError(
                "render_output_missing", "FFmpeg produced no render output", retryable=False
            ) from exc
        if size <= 0 or size > spec.max_output_bytes:
            await asyncio.to_thread(spec.destination.unlink, missing_ok=True)
            raise MediaPipelineError(
                "render_output_size_invalid",
                "render output size is empty or exceeds the configured limit",
                retryable=False,
            )
        if progress_callback is not None:
            await progress_callback(1.0)

    async def _read_render_progress(
        self,
        reader: asyncio.StreamReader,
        *,
        duration_seconds: float,
        callback: Callable[[float], Awaitable[None]] | None,
    ) -> None:
        consumed = 0
        last_fraction = -1.0
        while True:
            line = await reader.readline()
            if not line:
                return
            consumed += len(line)
            if consumed > self.settings.media_subprocess_output_bytes:
                raise _OutputLimitExceeded
            key, separator, value = line.decode("utf-8", errors="replace").strip().partition("=")
            if not separator or callback is None:
                continue
            fraction: float | None = None
            if key in {"out_time_us", "out_time_ms"}:
                try:
                    fraction = int(value) / 1_000_000 / max(duration_seconds, 0.001)
                except ValueError:
                    fraction = None
            elif key == "progress" and value == "end":
                fraction = 1.0
            if fraction is not None:
                bounded = max(0.0, min(1.0, fraction))
                if bounded >= 1.0 or bounded - last_fraction >= 0.01:
                    last_fraction = bounded
                    await callback(bounded)

    @staticmethod
    async def _monitor_render_cancellation(
        process: asyncio.subprocess.Process,
        callback: Callable[[], Awaitable[bool]] | None,
    ) -> None:
        while process.returncode is None:
            if callback is not None and await callback():
                raise MediaProcessCancelledError()
            await asyncio.sleep(0.25)

    @staticmethod
    def _duration(format_data: dict[str, Any], streams: list[dict[str, Any]]) -> float:
        candidates = [format_data.get("duration")]
        candidates.extend(item.get("duration") for item in streams)
        values: list[float] = []
        for value in candidates:
            try:
                number = float(str(value))
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0:
                values.append(number)
        return max(values, default=0.0)


def _ffmpeg_listing_contains(listing: str, capability: str) -> bool:
    return any(capability in line.split() for line in listing.splitlines())


def _escape_filter_path(path: Path) -> str:
    # Paths are server-generated and still escaped for FFmpeg's filter parser.
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _optional_int(value: Any) -> int | None:
    if value in {None, "", "N/A"}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_rate(value: Any) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            result = float(numerator) / denominator_value
        else:
            result = float(str(value))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return result if math.isfinite(result) and result >= 0 else None
