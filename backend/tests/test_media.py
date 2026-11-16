import pytest

from app.services import media
from app.services.media import MediaError


def test_probe_reads_streams(sample_video):
    info = media.probe(sample_video)
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (320, 240)
    assert info.video_codec == "h264"
    assert 2.5 < info.duration_seconds < 3.5
    assert info.size_bytes > 0


def test_probe_video_without_audio(silent_video):
    info = media.probe(silent_video)
    assert info.has_video and not info.has_audio


def test_probe_missing_and_corrupt_files(tmp_path):
    with pytest.raises(MediaError, match="not found"):
        media.probe(tmp_path / "nope.mp4")
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"this is not a video")
    with pytest.raises(MediaError, match="Could not read"):
        media.probe(corrupt)


def test_is_valid_media(sample_video, silent_video, tmp_path):
    assert media.is_valid_media(sample_video, need_video=True, need_audio=True)
    assert not media.is_valid_media(silent_video, need_video=True, need_audio=True)
    assert media.is_valid_media(silent_video, need_video=True, need_audio=False)
    empty = tmp_path / "empty.mp4"
    empty.touch()
    assert not media.is_valid_media(empty, need_video=False, need_audio=False)


def test_extract_audio_writes_16k_mono_wav_with_progress(sample_video, tmp_path):
    progress: list[float] = []
    out = media.extract_audio(sample_video, tmp_path / "audio.wav", duration_seconds=3,
                              on_progress=progress.append)
    assert out.is_file()
    assert not (tmp_path / "audio.partial.wav").exists()
    info = media.probe(out)
    assert info.has_audio and not info.has_video
    assert 2.5 < info.duration_seconds < 3.5
    # 16 kHz * 2 bytes * 3 s ≈ 96 KB of PCM
    assert 80_000 < info.size_bytes < 110_000
    assert progress and progress[-1] == 1.0


def test_extract_audio_fails_cleanly_without_audio(silent_video, tmp_path):
    with pytest.raises(MediaError, match="FFmpeg failed"):
        media.extract_audio(silent_video, tmp_path / "audio.wav")
    assert not (tmp_path / "audio.wav").exists()


def test_missing_ffmpeg_binary(monkeypatch, sample_video, tmp_path):
    monkeypatch.setenv("FFMPEG_PATH", "no-such-ffmpeg")
    with pytest.raises(MediaError, match="ffmpeg not found"):
        media.extract_audio(sample_video, tmp_path / "a.wav")


def test_load_wav_reads_extracted_audio(sample_video, tmp_path):
    from app.services.transcription import SAMPLE_RATE, TranscriptionError, load_wav

    wav = media.extract_audio(sample_video, tmp_path / "audio.wav")
    samples = load_wav(wav)
    assert samples.dtype.name == "float32"
    assert abs(len(samples) / SAMPLE_RATE - 3.0) < 0.1
    assert 0.01 < float(abs(samples).max()) <= 1.0  # the 440 Hz tone is audible

    with pytest.raises(TranscriptionError, match="Could not read"):
        load_wav(sample_video)  # an MP4 is not a WAV
