from app.services import media, render
from app.services.reframe import CropPlan
from app.services.sentences import Word


def test_filter_graph_track_layout_uses_moving_crop():
    plan = CropPlan("track", 1920, 1080, 608, 1080, ((0.0, 100), (2.0, 300)), 0.9)
    graph = render.build_filter_graph(plan, width=1080, height=1920, ass_file="01.ass")
    assert "crop=w=608:h=1080:x='" in graph and "gte(t,0.0)" in graph
    assert "scale=1080:1920" in graph and graph.endswith("ass=01.ass,format=yuv420p[v]")


def test_filter_graph_fit_layout_uses_blurred_background():
    plan = CropPlan("fit", 1920, 1080, 608, 1080, (), 0.0)
    graph = render.build_filter_graph(plan, width=1080, height=1920, ass_file="01.ass")
    assert "split=2" in graph and "gblur" in graph and "overlay" in graph


def test_render_clip_produces_vertical_video_captions_and_thumbnail(long_video, tmp_path):
    words = [Word(0.2, 0.6, " Hello"), Word(0.7, 1.2, " world."), Word(1.5, 2.0, " Again!")]
    progress = []

    result = render.render_clip(long_video, start=10.0, end=14.0, words=words,
                                out_dir=tmp_path / "clips", name="01", language="en",
                                on_progress=lambda f, m: progress.append((f, m)))

    info = media.probe(result.video)
    assert (info.width, info.height) == (216, 384)  # test resolution from conftest
    assert info.has_audio and info.video_codec == "h264"
    assert abs(info.duration_seconds - 4.0) < 0.3
    assert result.layout == "fit"  # the test pattern has no faces
    assert result.thumbnail.stat().st_size > 0
    assert "Hello world" in result.subtitles.read_text(encoding="utf-8")
    names = sorted(p.name for p in (tmp_path / "clips").iterdir())
    assert names == ["01.ass", "01.jpg", "01.mp4", "01.srt"]  # no partial/filter leftovers
    assert progress[-1] == (1.0, "Clip ready")
