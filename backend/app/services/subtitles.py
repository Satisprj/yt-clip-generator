"""Caption files for clips: word-highlighted ASS (burned in) and plain SRT (download).

Captions are built from the transcript's word timestamps, already shifted so 0 is the
start of the clip. Each caption shows a few words; the word being spoken is highlighted.
"""

from dataclasses import dataclass
from pathlib import Path

from app.services.sentences import Word, ends_sentence

HIGHLIGHT_COLOUR = "&H0000D7FF&"  # ASS colours are &HBBGGRR& -> warm yellow
SENTENCE_GAP = 0.6  # a pause this long starts a new caption
LINGER = 0.25       # keep the last caption on screen briefly after the final word

# libass picks the font by name; these cover scripts that Arial lacks on Windows.
LANGUAGE_FONTS = {
    "hi": "Nirmala UI", "ne": "Nirmala UI", "mr": "Nirmala UI", "bn": "Nirmala UI",
    "ta": "Nirmala UI", "te": "Nirmala UI", "gu": "Nirmala UI", "pa": "Nirmala UI",
    "zh": "Microsoft YaHei", "ja": "Yu Gothic", "ko": "Malgun Gothic", "th": "Leelawadee UI",
}
DEFAULT_FONT = "Arial"


@dataclass(frozen=True)
class Caption:
    start: float
    end: float
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(clean_word(w.text) for w in self.words)


def clean_word(text: str) -> str:
    """Trim spaces and soft trailing punctuation (keeps ? and !) for a cleaner look."""
    word = text.strip()
    while len(word) > 1 and word[-1] in ",.;:":
        word = word[:-1]
    return word


def font_for_language(language: str | None, override: str | None = None) -> str:
    if override:
        return override
    return LANGUAGE_FONTS.get((language or "").split("-")[0].lower(), DEFAULT_FONT)


def build_captions(
    words: list[Word],
    *,
    clip_duration: float,
    max_words: int = 3,
    max_chars: int = 18,
) -> list[Caption]:
    """Group words into short captions, breaking at sentence ends and pauses."""
    words = [w for w in words if w.text.strip() and w.end > 0 and w.start < clip_duration]
    groups: list[list[Word]] = []
    current: list[Word] = []
    for i, word in enumerate(words):
        if current:
            chars = sum(len(clean_word(w.text)) + 1 for w in current) + len(clean_word(word.text))
            if (len(current) >= max_words or chars > max_chars
                    or word.start - current[-1].end >= SENTENCE_GAP):
                groups.append(current)
                current = []
        current.append(word)
        if ends_sentence(word) and i + 1 < len(words):
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    captions: list[Caption] = []
    for i, group in enumerate(groups):
        start = max(0.0, group[0].start)
        if i + 1 < len(groups) and groups[i + 1][0].start - group[-1].end < SENTENCE_GAP:
            end = groups[i + 1][0].start  # back-to-back captions: no flicker between them
        else:
            end = group[-1].end + LINGER
        end = min(max(end, start + 0.1), clip_duration)
        captions.append(Caption(round(start, 3), round(end, 3), tuple(group)))
    return captions


def _ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(
    path: Path,
    captions: list[Caption],
    *,
    width: int,
    height: int,
    font: str = DEFAULT_FONT,
) -> Path:
    """Write an ASS file: one event per word, with the spoken word highlighted."""
    font_size = round(height * 0.044)    # ~84 px at 1920
    outline = max(2, round(height * 0.0031))
    margin_v = round(height * 0.28)      # lower-middle, clear of platform UI at the bottom
    margin_h = round(width * 0.07)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{font},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},2,{margin_h},{margin_h},"
        f"{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for caption in captions:
        words = caption.words
        for i, word in enumerate(words):
            start = caption.start if i == 0 else word.start
            end = words[i + 1].start if i + 1 < len(words) else caption.end
            if end <= start:
                continue
            parts = [
                f"{{\\c{HIGHLIGHT_COLOUR}}}{_ass_escape(clean_word(w.text))}{{\\r}}" if j == i
                else _ass_escape(clean_word(w.text))
                for j, w in enumerate(words)
            ]
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,"
                         + " ".join(parts))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, captions: list[Caption]) -> Path:
    blocks = [
        f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n"
        for i, c in enumerate(captions, start=1)
    ]
    # SRT conventionally uses CRLF line endings; written explicitly so every OS matches.
    path.write_text("\n".join(blocks), encoding="utf-8", newline="\r\n")
    return path
