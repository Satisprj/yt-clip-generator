from app.services.sentences import Word
from app.services.subtitles import (
    build_captions,
    clean_word,
    font_for_language,
    write_ass,
    write_srt,
)


def w(start, end, text):
    return Word(start, end, " " + text)


SPEECH = [w(0.0, 0.3, "This"), w(0.35, 0.6, "is"), w(0.65, 1.0, "amazing."),
          w(1.1, 1.4, "Nobody"), w(1.45, 1.7, "knew"), w(1.75, 2.0, "it,"),
          w(3.0, 3.4, "until"), w(3.45, 3.9, "today!")]


def test_clean_word():
    assert clean_word(" amazing.") == "amazing"
    assert clean_word(" it,") == "it"
    assert clean_word(" today!") == "today!"
    assert clean_word(" ...") == "."


def test_captions_break_on_sentences_pauses_and_size():
    captions = build_captions(SPEECH, clip_duration=10)
    assert [c.text for c in captions] == ["This is amazing", "Nobody knew it", "until today!"]
    # back-to-back captions touch (no flicker); after a pause the caption lingers briefly
    assert captions[0].end == captions[1].start == 1.1
    assert captions[1].end == 2.25
    assert captions[2].end == 4.15


def test_captions_respect_max_chars_and_clip_bounds():
    long_words = [w(i * 0.5, i * 0.5 + 0.4, "extraordinarily") for i in range(4)]
    assert all(len(c.words) == 1 for c in build_captions(long_words, clip_duration=10))
    clipped = build_captions([w(-0.5, -0.1, "gone"), w(0.0, 0.4, "kept"), w(9.8, 10.5, "end")],
                             clip_duration=10)
    assert [c.text for c in clipped] == ["kept", "end"]
    assert clipped[-1].end == 10


def test_ass_has_one_event_per_word_with_highlight(tmp_path):
    captions = build_captions(SPEECH[:3], clip_duration=5)
    text = write_ass(tmp_path / "c.ass", captions, width=1080, height=1920).read_text("utf-8")
    assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
    events = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 3
    assert events[0].startswith("Dialogue: 0,0:00:00.00,0:00:00.35,Caption")
    assert events[0].endswith("{\\c&H0000D7FF&}This{\\r} is amazing")
    assert events[2].endswith("This is {\\c&H0000D7FF&}amazing{\\r}")


def test_ass_escapes_override_braces(tmp_path):
    captions = build_captions([w(0, 1, "{\\b1}hack")], clip_duration=2)
    text = write_ass(tmp_path / "c.ass", captions, width=1080, height=1920).read_text("utf-8")
    assert "{\\b1}" not in text and "(\\\\b1)hack" in text


def test_srt_format(tmp_path):
    captions = build_captions(SPEECH, clip_duration=10)
    text = write_srt(tmp_path / "c.srt", captions).read_text("utf-8")
    assert text.startswith("1\n00:00:00,000 --> 00:00:01,100\nThis is amazing\n")
    assert "3\n00:00:03,000 --> 00:00:04,150\nuntil today!\n" in text


def test_font_for_language():
    assert font_for_language("en") == "Arial"
    assert font_for_language("ne") == "Nirmala UI"
    assert font_for_language("zh") == "Microsoft YaHei"
    assert font_for_language("ne", override="Mangal") == "Mangal"
