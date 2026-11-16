import pytest

from app.services.clip_selection import (
    Candidate,
    ClipWindow,
    fit_window,
    is_dangling_start,
    overlap_ratio,
    select_clips,
    viral_score,
    window_bounds,
)
from app.services.sentences import Sentence

GOOD = {"hook": 9, "standalone": 9, "payoff": 8, "emotion": 7, "shareability": 8}


def make_sentences(durations, gap=0.5, texts=None):
    """Consecutive sentences with the given lengths in seconds."""
    out, t = [], 0.0
    for i, d in enumerate(durations):
        text = texts[i] if texts else f"Sentence number {i}."
        out.append(Sentence(id=i, start=round(t, 3), end=round(t + d, 3), text=text,
                            first_word=i, last_word=i))
        t += d + gap
    return out


def fit(sentences, a, b, **kw):
    return fit_window(sentences, a, b, min_seconds=20, max_seconds=30,
                      video_duration=sentences[-1].end + 5, **kw)


def test_viral_score_weights_and_clamps():
    assert viral_score({k: 10 for k in GOOD}) == 10.0
    assert viral_score({}) == 1.0
    assert viral_score({"hook": 99, "standalone": -5}) == round(0.3 * 10 + 0.25 * 1 + 0.45, 1)
    assert viral_score(GOOD) == 8.4


def test_dangling_start_detection():
    assert is_dangling_start("And then it exploded.")
    assert is_dangling_start("  so, what happened next")
    assert is_dangling_start("That's why I quit.")
    assert not is_dangling_start("Andrew built a company.")
    assert not is_dangling_start("Nobody tells you this about money.")


def test_padding_never_overlaps_neighbours():
    s = make_sentences([5, 5, 5], gap=0.1)
    start, end = window_bounds(s, 1, 1, video_duration=100)
    assert start == s[0].end and end == s[2].start  # gaps smaller than the padding
    start, end = window_bounds(s, 0, 2, video_duration=s[2].end + 0.1)
    assert start == 0.0 and end == pytest.approx(s[2].end + 0.1)


def test_fit_keeps_window_that_already_fits():
    s = make_sentences([8] * 10)
    w = fit(s, 2, 4)  # 3 sentences ≈ 25 s
    assert (w.first_sentence, w.last_sentence) == (2, 4)
    assert 20 <= w.duration <= 30


def test_fit_extends_short_window_with_whole_sentences():
    s = make_sentences([8] * 10)
    w = fit(s, 3, 3)  # 8 s: too short
    assert 20 <= w.duration <= 30
    assert w.first_sentence <= 3 <= w.last_sentence


def test_fit_trims_long_window_preferring_to_keep_the_hook():
    s = make_sentences([8] * 10)
    w = fit(s, 2, 7)  # 6 sentences ≈ 50 s: too long
    assert 20 <= w.duration <= 30
    assert w.first_sentence == 2  # the chosen opening is kept; the end is trimmed


def test_fit_avoids_dangling_start():
    texts = ["Here is the secret.", "And it changed everything.", "Nobody expected it.",
             "It worked.", "The end."]
    s = make_sentences([9, 9, 9, 9, 9], texts=texts)
    w = fit(s, 1, 2)  # starts on "And ..."
    assert w.first_sentence == 0


def test_fit_uses_tolerance_then_gives_up():
    s = make_sentences([33])  # one 33 s sentence: within +4 s tolerance
    assert fit(s, 0, 0).duration == pytest.approx(33.35, abs=0.01)
    s = make_sentences([45])  # far too long and can't be split at sentence level
    assert fit(s, 0, 0) is None
    assert fit_window([], 0, 0, min_seconds=20, max_seconds=30, video_duration=10) is None


def test_fit_clamps_out_of_range_ids():
    s = make_sentences([8] * 5)
    w = fit(s, 3, 99)
    assert w.last_sentence <= 4 and 20 <= w.duration <= 30


def test_overlap_ratio():
    a, b = ClipWindow(0, 0, 0, 20), ClipWindow(0, 0, 15, 40)
    assert overlap_ratio(a, b) == pytest.approx(5 / 20)
    assert overlap_ratio(a, ClipWindow(0, 0, 25, 50)) == 0.0
    assert overlap_ratio(a, ClipWindow(0, 0, 5, 10)) == 1.0


def cand(a, b, category="funny", scores=GOOD):
    return Candidate(a, b, category, hook="h", reason="r", scores=scores)


def select(candidates, sentences, max_clips=10):
    return select_clips(candidates, sentences, min_seconds=20, max_seconds=30,
                        max_clips=max_clips, video_duration=sentences[-1].end + 5)


def test_select_ranks_by_score_and_drops_overlaps():
    s = make_sentences([8] * 30)
    low = {k: 4 for k in GOOD}
    clips = select([cand(0, 2, scores=low), cand(1, 3), cand(10, 12, "story"),
                    cand(20, 22, "educational", low)], s)
    # cand(0,2) overlaps the better cand(1,3) and is dropped
    assert [(c.window.first_sentence, c.rank) for c in clips] == [(1, 1), (10, 2), (20, 3)]
    assert clips[0].viral_score > clips[2].viral_score
    assert clips[0].text.startswith("Sentence number 1.")


def test_select_respects_max_clips_and_category_balance():
    s = make_sentences([8] * 60)
    candidates = [cand(i * 4, i * 4 + 2, "funny") for i in range(8)]
    candidates.append(cand(40, 42, "story", {k: 5 for k in GOOD}))
    clips = select(candidates, s, max_clips=4)
    assert len(clips) == 4
    # The lower-scoring "story" clip still makes it in for variety (soft cap of 2 funny
    # in the first pass); the last slot is filled with the next best funny clip.
    assert any(c.candidate.category == "story" for c in clips)
    assert sum(c.candidate.category == "funny" for c in clips) == 3
    assert [c.rank for c in clips] == [1, 2, 3, 4]
    assert clips[-1].candidate.category == "story"  # ranked by score


def test_select_fills_slots_when_only_one_category_exists():
    s = make_sentences([8] * 60)
    clips = select([cand(i * 4, i * 4 + 2, "funny") for i in range(8)], s, max_clips=6)
    assert len(clips) == 6


def test_select_skips_unfittable_candidates():
    s = make_sentences([45, 8, 8, 8])
    clips = select([cand(0, 0), cand(1, 3)], s)
    assert [c.window.first_sentence for c in clips] == [1]


def test_fit_never_drifts_away_from_the_chosen_moment():
    # Real transcript timings: the model picked S3 alone (an 18 s chorus). Moving to S1-S2
    # fit 20-30 s more cheaply but doesn't contain S3 at all, so the clip got the chorus's
    # hook and title over the verse's content. The chosen sentence must stay in the clip.
    times = [(3.34, 4.32), (18.04, 21.14), (22.41, 41.44), (41.44, 59.42), (60.0, 72.3),
             (73.0, 89.02), (89.28, 105.94)]
    s = [Sentence(i, a, b, f"Line {i}.", i, i) for i, (a, b) in enumerate(times)]
    w = fit_window(s, 3, 3, min_seconds=20, max_seconds=30, video_duration=213)
    assert w.first_sentence <= 3 <= w.last_sentence


def test_fit_keeps_at_least_half_of_a_multi_sentence_pick():
    s = make_sentences([8] * 20)
    w = fit(s, 5, 10)  # 6 sentences ≈ 50 s: must be trimmed
    kept = min(w.last_sentence, 10) - max(w.first_sentence, 5) + 1
    assert kept >= 3 and 20 <= w.duration <= 30
