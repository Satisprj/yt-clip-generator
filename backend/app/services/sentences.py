"""Group timestamped words into sentences.

Sentences are the units the AI picks clips from (by ID), so they must:
- end where the speaker ends a thought (punctuation, or a long pause), and
- stay short enough to build 20–30 s clips from (long run-ons are split at a comma or the
  longest pause).

Word texts keep Whisper's leading spaces (" Hello"), so joining them restores the text.
"""

from dataclasses import asdict, dataclass

SENTENCE_END = (".", "?", "!", "…", "。", "？", "！", "।", "॥")
CLOSING = "\"'”’)]»"
SOFT_BREAK = (",", ";", ":", "，", "、", "—", "–")
ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "mt.", "vs.", "etc.",
    "e.g.", "i.e.", "inc.", "ltd.", "co.", "corp.", "no.", "u.s.", "u.k.", "a.m.", "p.m.",
    "approx.", "dept.", "fig.", "jan.", "feb.", "aug.", "sept.", "oct.", "nov.", "dec.",
}


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str
    probability: float = 1.0


@dataclass(frozen=True)
class Sentence:
    id: int
    start: float
    end: float
    text: str
    first_word: int  # index into the word list (inclusive)
    last_word: int   # inclusive

    def to_dict(self) -> dict:
        return asdict(self)


def _bare(text: str) -> str:
    return text.strip().rstrip(CLOSING)


def ends_sentence(word: Word) -> bool:
    bare = _bare(word.text)
    if not bare.endswith(SENTENCE_END):
        return False
    return bare.lower() not in ABBREVIATIONS


def _soft_break(word: Word) -> bool:
    return _bare(word.text).endswith(SOFT_BREAK)


def _best_split(buffer: list[Word]) -> int:
    """Index after which to split an over-long sentence (never the last word)."""
    n = len(buffer)
    lower = max(1, int(n * 0.3))  # avoid splitting off a tiny fragment at the start
    for i in range(n - 2, lower - 2, -1):
        if _soft_break(buffer[i]):
            return i
    # No comma: split at the longest pause.
    gaps = [(buffer[i + 1].start - buffer[i].end, i) for i in range(max(0, lower - 1), n - 1)]
    return max(gaps)[1] if gaps else n // 2 - 1


def split_sentences(
    words: list[Word],
    *,
    max_gap: float = 1.2,
    max_duration: float = 20.0,
    max_words: int = 60,
) -> list[Sentence]:
    sentences: list[Sentence] = []
    buffer: list[Word] = []
    buffer_start = 0  # index of buffer[0] in `words`

    def emit(count: int) -> None:
        nonlocal buffer, buffer_start
        part = buffer[:count]
        text = "".join(w.text for w in part).strip()
        if text:
            sentences.append(Sentence(
                id=len(sentences),
                start=round(part[0].start, 3),
                end=round(part[-1].end, 3),
                text=" ".join(text.split()),
                first_word=buffer_start,
                last_word=buffer_start + count - 1,
            ))
        buffer = buffer[count:]
        buffer_start += count

    for i, word in enumerate(words):
        buffer.append(word)
        next_word = words[i + 1] if i + 1 < len(words) else None

        if next_word is None:
            break
        if ends_sentence(word) or next_word.start - word.end >= max_gap:
            emit(len(buffer))
            continue
        # Split run-on sentences; repeat in case the remainder is still too long.
        while len(buffer) > 1 and (
            buffer[-1].end - buffer[0].start > max_duration or len(buffer) > max_words
        ):
            emit(_best_split(buffer) + 1)

    while buffer:
        if len(buffer) > 1 and (buffer[-1].end - buffer[0].start > max_duration
                                or len(buffer) > max_words):
            emit(_best_split(buffer) + 1)
        else:
            emit(len(buffer))
    return sentences
