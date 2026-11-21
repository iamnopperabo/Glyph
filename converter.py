"""Media decoding and ASCII rendering.

Kept separate from Flask so the interesting part can be tested without
starting a server.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

import imageio.v2 as imageio

RAMPS = {
    "classic": "@%#*+=-:. ",
    "fine": "@&%QWNM0gB$#*+=-:. ",
    "blocks": "█▓▒░ ",
    "minimal": "#*+-. ",
    "binary": "10 ",
}

DEFAULT_RAMP = "classic"

# A monospace cell is taller than it is wide. Without this correction,
# portraits come out stretched like a bad scan.
CHAR_ASPECT = 0.52
MAX_ROWS = 220

VIDEO_SUFFIXES = {
    ".mp4",
    ".m4v",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
    ".wmv",
    ".flv",
    ".mpg",
    ".mpeg",
}


class MediaError(Exception):
    """An error that is safe to show to the person using the app."""


@dataclass(frozen=True)
class MediaInfo:
    kind: str
    format: str
    width: int
    height: int
    frames: int
    duration_ms: int | None = None
    fps: float | None = None


@dataclass(frozen=True)
class AsciiOptions:
    width: int = 100
    ramp: str = DEFAULT_RAMP
    invert: bool = False
    brightness: float = 1.0
    contrast: float = 1.0
    fps: int = 12


@dataclass(frozen=True)
class Conversion:
    columns: int
    rows: int
    frames: list[str]
    delays: list[int]
    trimmed: bool


def inspect_media(path: Path, name: str, content_type: str = "") -> MediaInfo:
    suffix = Path(name).suffix.lower()

    try:
        with Image.open(path) as image:
            image_format = (image.format or "").upper()
            if image_format:
                width, height = image.size
                frame_count = int(getattr(image, "n_frames", 1) or 1)
                if frame_count > 1:
                    return MediaInfo(
                        kind="animation",
                        format=image_format,
                        width=width,
                        height=height,
                        frames=frame_count,
                        duration_ms=_animation_duration(image, frame_count),
                    )
                return MediaInfo(
                    kind="image",
                    format=image_format,
                    width=width,
                    height=height,
                    frames=1,
                )
    except Image.DecompressionBombError as exc:
        raise MediaError("That image is too large to process safely.") from exc
    except (UnidentifiedImageError, OSError, ValueError):
        pass

    if suffix in VIDEO_SUFFIXES or content_type.startswith("video/"):
        return _inspect_video(path, suffix)

    raise MediaError(
        "Unsupported file. Try PNG, JPEG, GIF, WebP, BMP, TIFF, MP4, MOV or WebM."
    )


def convert_media(
    path: Path,
    kind: str,
    options: AsciiOptions,
    max_frames: int = 240,
    max_seconds: float = 20.0,
) -> Conversion:
    if kind == "video":
        return _convert_video(path, options, max_frames, max_seconds)
    if kind == "animation":
        return _convert_animation(path, options, max_frames, max_seconds)
    return _convert_image(path, options)


def _convert_image(path: Path, options: AsciiOptions) -> Conversion:
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            rendered = _render_image(_flatten(image), options)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise MediaError("That image looks broken. Try another file.") from exc

    columns, rows = _output_size(image.width, image.height, options.width)
    return Conversion(columns, rows, [rendered], [], False)


def _convert_animation(
    path: Path,
    options: AsciiOptions,
    max_frames: int,
    max_seconds: float,
) -> Conversion:
    frames: list[str] = []
    delays: list[int] = []
    trimmed = False
    elapsed_ms = 0

    try:
        with Image.open(path) as image:
            total_frames = int(getattr(image, "n_frames", 1) or 1)
            for index in range(total_frames):
                image.seek(index)
                delay = _frame_delay(image)
                if frames and (elapsed_ms + delay) / 1000 > max_seconds:
                    trimmed = True
                    break
                if len(frames) >= max_frames:
                    trimmed = True
                    break

                rendered = _render_image(_flatten(image), options)
                if not frames:
                    columns, rows = _output_size(image.width, image.height, options.width)
                frames.append(rendered)
                delays.append(delay)
                elapsed_ms += delay

            if len(frames) < total_frames:
                trimmed = True
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise MediaError("Couldn't read that animation. Try re-saving it as a GIF.") from exc

    if not frames:
        raise MediaError("That animation has no readable frames.")

    return Conversion(columns, rows, frames, delays, trimmed)


def _convert_video(
    path: Path,
    options: AsciiOptions,
    max_frames: int,
    max_seconds: float,
) -> Conversion:
    frames: list[str] = []
    delays: list[int] = []
    trimmed = False
    columns = rows = 0
    reader = None

    try:
        reader = imageio.get_reader(str(path), format="ffmpeg")
        meta = reader.get_meta_data()
        source_fps = _positive_float(meta.get("fps"), 24.0)
        target_fps = max(1, options.fps)
        step = max(1, round(source_fps / target_fps))
        delay = max(20, round(1000 * step / source_fps))

        for index, raw_frame in enumerate(reader.iter_data()):
            if index % step:
                continue
            if index / source_fps >= max_seconds:
                trimmed = True
                break
            if len(frames) >= max_frames:
                trimmed = True
                break

            frame = _array_to_image(raw_frame)
            rendered = _render_image(frame, options)
            if not frames:
                columns, rows = _output_size(frame.width, frame.height, options.width)
            frames.append(rendered)
            delays.append(delay)
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError(
            "Couldn't read that video. Try re-encoding it as MP4 (H.264)."
        ) from exc
    finally:
        if reader is not None:
            reader.close()

    if not frames:
        raise MediaError("That video has no readable frames.")

    return Conversion(columns, rows, frames, delays, trimmed)


def _render_image(image: Image.Image, options: AsciiOptions) -> str:
    columns, rows = _output_size(image.width, image.height, options.width)

    gray = _flatten(image).convert("L")
    gray = gray.resize((columns, rows), Image.Resampling.LANCZOS)

    if options.brightness != 1.0:
        gray = ImageEnhance.Brightness(gray).enhance(options.brightness)
    if options.contrast != 1.0:
        gray = ImageEnhance.Contrast(gray).enhance(options.contrast)

    ramp = RAMPS.get(options.ramp, RAMPS[DEFAULT_RAMP])
    if options.invert:
        ramp = ramp[::-1]

    last = len(ramp) - 1
    lookup = [ramp[(value * last) // 255] for value in range(256)]
    pixels = gray.tobytes()

    lines = []
    for row in range(rows):
        start = row * columns
        line = "".join(lookup[value] for value in pixels[start : start + columns])
        lines.append(line.rstrip())

    return "\n".join(lines)


def _output_size(width: int, height: int, columns: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise MediaError("That file has no image data.")

    columns = max(1, columns)
    rows = round(columns * (height / width) * CHAR_ASPECT)
    rows = min(MAX_ROWS, max(1, rows))
    return columns, rows


def _flatten(image: Image.Image) -> Image.Image:
    """Turn transparency into black before it reaches the luminance pass."""
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def _array_to_image(frame) -> Image.Image:
    if frame is None or not hasattr(frame, "shape"):
        raise MediaError("The video decoder returned an empty frame.")
    if len(frame.shape) == 2:
        return Image.fromarray(frame)
    if frame.shape[2] == 4:
        return Image.fromarray(frame, "RGBA")
    return Image.fromarray(frame)


def _inspect_video(path: Path, suffix: str) -> MediaInfo:
    reader = None
    try:
        reader = imageio.get_reader(str(path), format="ffmpeg")
        meta = reader.get_meta_data()
        size = meta.get("size")

        if not size:
            first = reader.get_data(0)
            height, width = first.shape[:2]
        else:
            width, height = int(size[0]), int(size[1])

        fps = _positive_float(meta.get("fps"), 0.0)
        duration = _positive_float(meta.get("duration"), 0.0)
        raw_frames = meta.get("nframes")
        frame_count = 0
        if isinstance(raw_frames, (int, float)) and math.isfinite(raw_frames):
            frame_count = int(raw_frames)

        if duration <= 0 and fps > 0 and frame_count > 0:
            duration = frame_count / fps

        return MediaInfo(
            kind="video",
            format=suffix.lstrip(".").upper() or "VIDEO",
            width=width,
            height=height,
            frames=frame_count,
            duration_ms=round(duration * 1000) if duration > 0 else None,
            fps=fps or None,
        )
    except Exception as exc:
        raise MediaError(
            "Couldn't read that video. Try an MP4 (H.264) file."
        ) from exc
    finally:
        if reader is not None:
            reader.close()


def _animation_duration(image: Image.Image, frame_count: int) -> int:
    total = 0
    for index in range(frame_count):
        image.seek(index)
        total += _frame_delay(image)
    return total


def _frame_delay(image: Image.Image) -> int:
    raw = image.info.get("duration", 100)
    try:
        delay = int(raw)
    except (TypeError, ValueError):
        delay = 100
    return min(1000, max(30, delay or 100))


def _positive_float(value, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number) or number <= 0:
        return fallback
    return number
