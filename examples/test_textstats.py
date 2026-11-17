"""Battery for textstats.py: one line per check, exit code 1 if any check failed.

Run:  python test_textstats.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import textstats  # noqa: E402

FAILED = []


def check(name, cond):
    if not cond:
        FAILED.append(name)
    print("%s %s" % ("[OK]  " if cond else "[FAIL]", name))


check("t1 word-count", textstats.word_count("a b  c") == 3)
check("t2 word-count-empty", textstats.word_count("") == 0)
check("t3 unique-words-ignore-repeats", textstats.unique_words("a A b a") == 2)
check("t4 unique-words-empty", textstats.unique_words("") == 0)
check("t5 mean-length", textstats.mean_length(["ab", "abcd"]) == 3.0)
# called without try/except on purpose: if the empty-list guard goes, this line raises,
# and the whole battery dies with a traceback instead of reporting a failed check
check("t6 mean-length-empty", textstats.mean_length([]) == 0.0)

sys.exit(1 if FAILED else 0)
