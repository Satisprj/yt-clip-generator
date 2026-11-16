"""Transcription service logic with a stand-in model (no Whisper download needed)."""

from types import SimpleNamespace

from app.services import media, transcription


class FakeModel:
    """Returns no words when VAD is on (like music-heavy audio), words when it's off."""

    def __init__(self):
        self.vad_calls = []

    def transcribe(self, audio, **options):
        self.vad_calls.append(options["vad_filter"])
        info = SimpleNamespace(language="en", language_probability=0.9, duration=3.0)
        if options["vad_filter"]:
            return iter([]), info
        word = SimpleNamespace(word=" Never", start=0.5, end=0.9, probability=0.8)
        return iter([SimpleNamespace(end=3.0, words=[word])]), info


def test_retries_without_vad_when_vad_removes_everything(monkeypatch, sample_video, tmp_path):
    model = FakeModel()
    monkeypatch.setattr(transcription, "get_model", lambda: model)
    wav = media.extract_audio(sample_video, tmp_path / "audio.wav")
    progress = []

    result = transcription.transcribe(wav, on_progress=progress.append)

    assert model.vad_calls == [True, False]
    assert [w.text for w in result.words] == [" Never"]
    assert progress[-1] == 1.0


def test_no_progress_completion_when_nothing_found(monkeypatch, sample_video, tmp_path):
    model = FakeModel()
    model.transcribe = lambda audio, **o: (iter([]), SimpleNamespace(
        language="en", language_probability=0.5, duration=3.0))
    monkeypatch.setattr(transcription, "get_model", lambda: model)
    wav = media.extract_audio(sample_video, tmp_path / "audio.wav")
    progress = []

    result = transcription.transcribe(wav, on_progress=progress.append)

    assert result.words == []
    assert 1.0 not in progress
