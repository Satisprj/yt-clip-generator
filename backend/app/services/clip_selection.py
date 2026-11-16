"""Turn the AI's suggested moments into final clips (pure logic, no I/O).

The model suggests sentence ranges with scores. This module
- fits each range to the allowed duration by adding/removing whole sentences, so clips
  never start or end mid-sentence,
- avoids starting on words that depend on earlier context ("And so...", "Which is why..."),
- scores, de-duplicates overlapping clips, balances categories and ranks the result.
"""

import math
from dataclasses import dataclass, field

from app.services.sentences import Sentence

RUBRIC_WEIGHTS = {
    "hook": 0.30,          # do the first seconds grab attention?
    "standalone": 0.25,    # understandable with no context?
    "payoff": 0.20,        # punchline, insight or resolution?
    "emotion": 0.15,       # energy, humor, emotion
    "shareability": 0.10,  # would people share or comment?
}

DANGLING_STARTS = (
    "and ", "and,", "but ", "so ", "so,", "because ", "which ", "that's why", "that is why",
    "then ", "or ", "also ", "plus ", "anyway", "like i said", "as i said", "exactly",
    "yeah", "yes.", "right.", "no.", "it's ", "that's ", "this is why", "he ", "she ",
    "they ", "it ", "those ", "these ",
)

START_PAD = 0.15  # seconds of breathing room before the first word
END_PAD = 0.35    # and after the last word


@dataclass(frozen=True)
class Candidate:
    """A moment suggested by the model (sentence IDs are inclusive)."""

    start_sentence: int
    end_sentence: int
    category: str
    hook: str
    reason: str
    scores: dict[str, int]


@dataclass(frozen=True)
class ClipWindow:
    first_sentence: int
    last_sentence: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class SelectedClip:
    candidate: Candidate
    window: ClipWindow
    viral_score: float
    text: str
    rank: int = 0
    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)


def viral_score(scores: dict[str, int]) -> float:
    """Weighted rubric score on a 0–10 scale (missing criteria count as 1)."""
    total = sum(w * min(10, max(1, int(scores.get(k, 1)))) for k, w in RUBRIC_WEIGHTS.items())
    return round(total, 1)


def is_dangling_start(text: str) -> bool:
    lower = text.strip().lower()
    return lower.startswith(DANGLING_STARTS)


def window_bounds(sentences: list[Sentence], first: int, last: int,
                  video_duration: float) -> tuple[float, float]:
    """Start/end times with padding that never reaches into neighbouring sentences."""
    start = sentences[first].start - START_PAD
    if first > 0:
        start = max(start, sentences[first - 1].end)
    end = sentences[last].end + END_PAD
    if last + 1 < len(sentences):
        end = min(end, sentences[last + 1].start)
    return max(0.0, round(start, 3)), round(min(end, video_duration), 3)


def fit_window(
    sentences: list[Sentence],
    start_id: int,
    end_id: int,
    *,
    min_seconds: float,
    max_seconds: float,
    video_duration: float,
    search: int = 3,
    tolerance: tuple[float, float] = (2.0, 4.0),
) -> ClipWindow | None:
    """Closest sentence-aligned window to [start_id, end_id] whose length fits the limits.

    Tries moving each edge by up to `search` sentences. Moving the start later (losing the
    chosen hook) costs more than adding context before it; starting on a dangling word is
    penalised. Durations slightly outside the limits (by `tolerance` seconds below/above)
    are accepted only if nothing fits exactly.
    """
    n = len(sentences)
    if n == 0:
        return None
    start_id = max(0, min(start_id, n - 1))
    end_id = max(start_id, min(end_id, n - 1))
    target = (min_seconds + max_seconds) / 2
    lo, hi = min_seconds - tolerance[0], max_seconds + tolerance[1]
    # The fitted clip must still be the moment the model chose: keep at least half of its
    # sentences (a one-sentence pick must stay in the clip).
    required_overlap = math.ceil((end_id - start_id + 1) / 2)

    best: tuple[float, ClipWindow] | None = None
    for a in range(max(0, start_id - search), min(end_id, start_id + search) + 1):
        for b in range(max(a, end_id - search), min(n - 1, end_id + search) + 1):
            if min(b, end_id) - max(a, start_id) + 1 < required_overlap:
                continue
            start, end = window_bounds(sentences, a, b, video_duration)
            duration = end - start
            if duration < lo or duration > hi:
                continue
            cost = 1.5 * (start_id - a) if a < start_id else 2.5 * (a - start_id)
            cost += 1.0 * abs(b - end_id)
            if is_dangling_start(sentences[a].text):
                cost += 2.0
            if not min_seconds <= duration <= max_seconds:
                cost += 3.0 + min(abs(duration - min_seconds), abs(duration - max_seconds))
            cost += 0.05 * abs(duration - target)
            if best is None or cost < best[0]:
                best = (cost, ClipWindow(a, b, start, end))
    return best[1] if best else None


def overlap_ratio(a: ClipWindow, b: ClipWindow) -> float:
    """Overlap as a fraction of the shorter clip (0 = disjoint, 1 = one contains the other)."""
    overlap = min(a.end, b.end) - max(a.start, b.start)
    if overlap <= 0:
        return 0.0
    return overlap / max(min(a.duration, b.duration), 1e-6)


def select_clips(
    candidates: list[Candidate],
    sentences: list[Sentence],
    *,
    min_seconds: float,
    max_seconds: float,
    max_clips: int,
    video_duration: float,
    max_overlap: float = 0.3,
) -> list[SelectedClip]:
    """Fit, score, de-duplicate and rank candidates. Returns at most `max_clips` clips."""
    fitted: list[SelectedClip] = []
    for c in candidates:
        window = fit_window(sentences, c.start_sentence, c.end_sentence,
                            min_seconds=min_seconds, max_seconds=max_seconds,
                            video_duration=video_duration)
        if window is None:
            continue
        text = " ".join(s.text for s in sentences[window.first_sentence:window.last_sentence + 1])
        score = viral_score(c.scores)
        if is_dangling_start(sentences[window.first_sentence].text):
            score = round(max(0.0, score - 0.5), 1)
        fitted.append(SelectedClip(candidate=c, window=window, viral_score=score, text=text))

    fitted.sort(key=lambda clip: (-clip.viral_score, clip.window.start))
    per_category_limit = max(2, math.ceil(max_clips * 0.5))
    kept: list[SelectedClip] = []
    category_counts: dict[str, int] = {}

    def overlaps(clip: SelectedClip) -> bool:
        return any(overlap_ratio(clip.window, k.window) > max_overlap for k in kept)

    # Pass 1: best clips with a soft cap per category, for variety.
    for clip in fitted:
        if len(kept) >= max_clips:
            break
        category = clip.candidate.category
        if overlaps(clip) or category_counts.get(category, 0) >= per_category_limit:
            continue
        kept.append(clip)
        category_counts[category] = category_counts.get(category, 0) + 1
    # Pass 2: if slots remain (e.g. every good moment is funny), fill them anyway.
    for clip in fitted:
        if len(kept) >= max_clips:
            break
        if clip not in kept and not overlaps(clip):
            kept.append(clip)

    kept.sort(key=lambda clip: (-clip.viral_score, clip.window.start))
    for rank, clip in enumerate(kept, start=1):
        clip.rank = rank
    return kept
