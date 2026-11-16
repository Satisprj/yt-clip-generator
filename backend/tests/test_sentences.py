from app.services.sentences import Word, ends_sentence, split_sentences


def words_from(text: str, *, start: float = 0.0, step: float = 0.4, gaps: dict | None = None):
    """Build evenly spaced words; `gaps` adds extra silence before word index -> seconds."""
    out, t = [], start
    for i, token in enumerate(text.split()):
        t += (gaps or {}).get(i, 0.0)
        out.append(Word(start=round(t, 3), end=round(t + step - 0.05, 3), text=" " + token))
        t += step
    return out


def texts(sentences):
    return [s.text for s in sentences]


def assert_covers_all(sentences, words):
    """Sentences are contiguous, in order, and cover every word exactly once."""
    expected = 0
    for i, s in enumerate(sentences):
        assert s.id == i
        assert s.first_word == expected and s.last_word >= s.first_word
        assert s.start == words[s.first_word].start and s.end == words[s.last_word].end
        expected = s.last_word + 1
    assert expected == len(words)


def test_splits_on_punctuation():
    words = words_from("Hello there. How are you? I'm fine! Great.")
    result = split_sentences(words)
    assert texts(result) == ["Hello there.", "How are you?", "I'm fine!", "Great."]
    assert_covers_all(result, words)


def test_keeps_abbreviations_and_closing_quotes():
    words = words_from('Dr. Smith met Mr. Jones, e.g. yesterday. She said "wow." Then left.')
    assert texts(split_sentences(words)) == [
        "Dr. Smith met Mr. Jones, e.g. yesterday.", 'She said "wow."', "Then left."]


def test_splits_on_long_pause_without_punctuation():
    words = words_from("so we started the company and then everything changed",
                       gaps={5: 2.0})
    result = split_sentences(words)
    assert texts(result) == ["so we started the company", "and then everything changed"]
    assert_covers_all(result, words)


def test_long_sentence_splits_at_comma():
    first = " ".join(f"a{i}" for i in range(30)) + ","
    second = " ".join(f"b{i}" for i in range(30)) + "."
    words = words_from(f"{first} {second}", step=0.5)  # 60 words, 30 s
    result = split_sentences(words, max_duration=20)
    assert result[0].text.endswith("a29,")
    assert all(s.end - s.start <= 20 for s in result)
    assert_covers_all(result, words)


def test_long_sentence_without_comma_splits_at_longest_pause():
    words = words_from(" ".join(f"w{i}" for i in range(50)), step=0.5, gaps={20: 0.8})
    result = split_sentences(words, max_duration=20)
    assert result[0].last_word == 19
    assert all(s.end - s.start <= 20 for s in result)
    assert_covers_all(result, words)


def test_max_words_limit():
    words = words_from(" ".join(f"w{i}" for i in range(130)), step=0.1)
    result = split_sentences(words, max_words=60, max_duration=999)
    assert all(s.last_word - s.first_word + 1 <= 60 for s in result)
    assert_covers_all(result, words)


def test_other_scripts():
    words = words_from("नमस्ते। तपाईंलाई कस्तो छ? 你好。 再见！")
    assert texts(split_sentences(words)) == ["नमस्ते।", "तपाईंलाई कस्तो छ?", "你好。", "再见！"]


def test_edge_cases():
    assert split_sentences([]) == []
    single = [Word(1.0, 1.5, " Hi")]
    result = split_sentences(single)
    assert texts(result) == ["Hi"]
    assert (result[0].start, result[0].end) == (1.0, 1.5)
    assert not ends_sentence(Word(0, 1, " etc."))
    assert ends_sentence(Word(0, 1, ' done."'))
