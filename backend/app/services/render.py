"""Render one clip: cut, reframe to 9:16, burn captions, normalise audio, encode."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.services import media, reframe
from app.services.sentences import Word
from app.services.subtitles import build_captions, font_for_language, write_ass, write_srt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderResult:
    video: Path
    subtitles: Path
    thumbnail: Path
    layout: str
    face_ratio: float


def build_filter_graph(plan: reframe.CropPlan, *, width: int, height: int,
                       ass_file: str) -> str:
    """FFmpeg filter graph (written to a file, so no shell escaping is needed)."""
    if plan.layout == "track":
        x = reframe.crop_x_expression(plan.keyframes)
        return (
            f"[0:v]crop=w={plan.crop_width}:h={plan.crop_height}:x='{x}':y=0,"
            f"scale={width}:{height}:flags=lanczos,setsar=1,"
            f"ass={ass_file},format=yuv420p[v]"
        )
    # "fit": the whole frame, centred over a blurred, zoomed copy of itself.
    return (
        "[0:v]split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=40,eq=brightness=-0.12[bgb];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1,ass={ass_file},format=yuv420p[v]"
    )


def render_clip(
    source: Path,
    *,
    start: float,
    end: float,
    words: list[Word],
    out_dir: Path,
    name: str,
    language: str | None,
    on_progress: Callable[[float, str], None] | None = None,
) -> RenderResult:
    """Render `source[start:end]` to `<out_dir>/<name>.mp4` (+ .ass, .srt, .jpg).

    `words` must already be relative to the clip (0 = clip start).
    """
    settings = get_settings()
    report = on_progress or (lambda fraction, message: None)
    source = source.resolve()  # FFmpeg runs inside out_dir, so relative paths would break
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = end - start

    report(0.0, "Finding the speaker")
    plan = reframe.analyze_clip(source, start, end)

    captions = build_captions(words, clip_duration=duration)
    font = font_for_language(language, settings.subtitle_font)
    ass = write_ass(out_dir / f"{name}.ass", captions, width=settings.render_width,
                    height=settings.render_height, font=font)
    srt = write_srt(out_dir / f"{name}.srt", captions)
    graph = out_dir / f"{name}.filter.txt"
    graph.write_text(build_filter_graph(plan, width=settings.render_width,
                                        height=settings.render_height, ass_file=ass.name),
                     encoding="utf-8")

    partial = out_dir / f"{name}.partial.mp4"
    video = out_dir / f"{name}.mp4"
    report(0.2, "Rendering video")
    media.run_ffmpeg(
        [
            "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
            "-/filter_complex", graph.name,
            "-map", "[v]", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", settings.render_preset,
            "-crf", str(settings.render_crf), "-profile:v", "high",
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            partial.name,
        ],
        duration_seconds=duration,
        on_progress=lambda f: report(0.2 + 0.75 * f, f"Rendering video: {f:.0%}"),
        cwd=out_dir,
    )
    partial.replace(video)
    graph.unlink(missing_ok=True)

    thumbnail = out_dir / f"{name}.jpg"
    media.run_ffmpeg(
        ["-ss", f"{min(1.0, duration / 2):.3f}", "-i", video.name, "-frames:v", "1",
         "-q:v", "3", thumbnail.name],
        cwd=out_dir,
        timeout_seconds=120,
    )
    report(1.0, "Clip ready")
    logger.info("Rendered %s (%s layout, %.1fs, faces %.0f%%)", video.name, plan.layout,
                duration, plan.face_ratio * 100)
    return RenderResult(video, srt, thumbnail, plan.layout, plan.face_ratio)
