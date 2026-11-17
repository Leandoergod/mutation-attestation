"""A toy subject for mutate_check: three small text helpers."""

READY = True  # module-level line; the no-start mutant replaces it with an exit on import
MAX_WORDS = 1000  # a documented limit that the test never checks


def word_count(text):
    if not text:
        return 0
    return len(text.split())


def unique_words(text):
    if not text:
        return 0
    seen = []
    for word in text.lower().split():
        if word not in seen:
            seen.append(word)
    return len(seen)


def mean_length(words):
    if not words:
        return 0.0
    return sum(len(w) for w in words) / len(words)
