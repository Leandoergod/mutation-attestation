# -*- coding: utf-8 -*-
"""Battery for mutate_check.py.

Run:  python test_mutate_check.py      (from this folder or any other)

The live subject is the toy example in `examples/` (`textstats.py` + `test_textstats.py`).
Three real mutants run on it, each aimed at its own outcome:

  HOLE-max-words-const       - changes `MAX_WORDS`, a value the toy battery NEVER checks
                               -> must stay a HOLE.
  COVERED-unique-always-adds - `if word not in seen:` -> `if True:` -> caught by check t3.
  CRASH-mean-guard-removed   - removes the empty-list guard in `mean_length()`; check t6
                               calls it WITHOUT try/except right in the battery body -> the
                               battery process dies on an unhandled ZeroDivisionError. A crash
                               is not a check and must count neither as a hole nor as coverage.

The other two outcomes are held by synthetic subjects in temporary folders: "no start"
(section 10: `sys.exit(2)` on import) and SKIPPED (sections 3, 7, 12: an anchor with 0 or 2+
occurrences).
Sections (headers in `main()`): 1 three outcomes on the live subject (2.1 - originals untouched) -
3 anchor guard - 4 line endings - 5 baseline not green - 6 CLI exit code - 7 overlapping anchor
occurrences - 8 durable record (`--save`/`--out`) - 9 bytecode cache - 10 "no start" -
11 `--help` - 12 `--anchors-only` - 13 `--only <names>` - 14 `--timeout` without a value or not
an integer -> code 2 (inside block 13).
Every CLI check that may write a file runs on a COPY of the subject in a temporary folder, so a
broken tool writes next to the copy, never into this repository.
The summary prints the number of executed checks; zero checks is code 2 "could not check",
not "clean".
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mutate_check as mc  # noqa: E402

MODULE = os.path.join(HERE, "mutate_check.py")
EXAMPLES = os.path.join(HERE, "examples")
TOY = os.path.join(EXAMPLES, "textstats.py")
TOY_TEST = os.path.join(EXAMPLES, "test_textstats.py")

PASS, FAIL = [], []

# Taken through getattr, so that on a version of the module without this outcome the checks of
# section 10 turn red as checks instead of dying with AttributeError on import.
NOSTART = getattr(mc, "NOSTART", "<no NOSTART outcome in the module>")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                        "" if cond else "  <- " + detail))


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


LIVE_MUTANTS = [
    {"name": "HOLE-max-words-const",
     "anchor": "MAX_WORDS = 1000  # a documented limit that the test never checks\n",
     "replace": "MAX_WORDS = 1  # a documented limit that the test never checks\n"},
    {"name": "COVERED-unique-always-adds",
     "anchor": "        if word not in seen:\n",
     "replace": "        if True:\n"},
    {"name": "CRASH-mean-guard-removed",
     "anchor": "    if not words:\n        return 0.0\n",
     "replace": "    pass  # guard removed\n"},
]

# an anchor that occurs exactly twice in the toy subject (both helpers open with it)
DUP_ANCHOR = "    if not text:\n        return 0\n"
DUP_COUNT = 2


def run_live(module):
    return module.run_mutations(TOY, TOY_TEST, LIVE_MUTANTS, timeout=60)


def toy_copy(dst):
    """A copy of the toy subject and its battery in folder dst. CLI checks run on such a copy:
    a mutant that "always saves" or "loses --out" puts its file next to the COPY."""
    os.makedirs(dst, exist_ok=True)
    shutil.copy(TOY, dst)
    shutil.copy(TOY_TEST, dst)
    return os.path.join(dst, "textstats.py"), os.path.join(dst, "test_textstats.py")


def stamp(path):
    """Fingerprint of a result file: (exists, size, mtime). A bare `os.path.exists` is not
    enough - the file may be left over from an EARLIER run; an overwrite changes size/mtime."""
    try:
        st = os.stat(path)
        return (True, st.st_size, st.st_mtime_ns)
    except OSError:
        return (False, None, None)


def main():
    # =============================================================== 1. THREE OUTCOMES, LIVE SUBJECT
    md5_before = {TOY: md5(TOY), TOY_TEST: md5(TOY_TEST)}
    res = run_live(mc)
    md5_after = {TOY: md5(TOY), TOY_TEST: md5(TOY_TEST)}

    check("1.0 baseline (unmutated toy subject) - the battery is green", res["baseline_ok"],
          str(res.get("baseline_rc")))
    by_name = {r["name"]: r for r in res["rows"]}

    check("1.1 must turn red: the MAX_WORDS mutant is a real HOLE",
          by_name["HOLE-max-words-const"]["outcome"] == mc.HOLE,
          str(by_name.get("HOLE-max-words-const")))
    check("1.2 must miss: unique-always-adds is COVERED, not a hole",
          by_name["COVERED-unique-always-adds"]["outcome"] == mc.COVERED,
          str(by_name.get("COVERED-unique-always-adds")))
    check("1.3 red tests are named, including the distinguishing check t3",
          any("t3" in c for c in by_name["COVERED-unique-always-adds"]["cases"]),
          str(by_name["COVERED-unique-always-adds"]["cases"]))
    check("1.4 battery crash (unhandled ZeroDivisionError) - NOT COUNTED, neither hole nor coverage",
          by_name["CRASH-mean-guard-removed"]["outcome"] == mc.CRASH,
          str(by_name.get("CRASH-mean-guard-removed")))
    check("1.5 crash and hole are DIFFERENT labels, not merged",
          by_name["CRASH-mean-guard-removed"]["outcome"]
          != by_name["HOLE-max-words-const"]["outcome"])
    check("1.6 coverage is not confused with a crash",
          by_name["COVERED-unique-always-adds"]["outcome"] != mc.CRASH)
    check("1.7 counters match the rows (1 HOLE, 1 COVERED, 1 CRASH, 0 NO START, 0 SKIPPED)",
          res["counts"] == {mc.COVERED: 1, mc.CRASH: 1, NOSTART: 0, mc.HOLE: 1, mc.SKIPPED: 0},
          str(res["counts"]))
    check("1.8 verdict_code=1 (a hole and a crash - not \"all clean\")", res["verdict_code"] == 1)
    check("2.1 originals NOT touched (md5 before = after)", md5_before == md5_after,
          "%s vs %s" % (md5_before, md5_after))

    # ============================================================= 3. ANCHOR GUARD
    r_missing = mc.run_mutations(TOY, TOY_TEST,
                                  [{"name": "x", "anchor": "no such line anywhere\n",
                                    "replace": "y\n"}], timeout=30)
    check("3.1 anchor with 0 occurrences - SKIPPED, not applied",
          r_missing["rows"][0]["outcome"] == mc.SKIPPED, str(r_missing["rows"]))

    r_dup = mc.run_mutations(TOY, TOY_TEST,
                              [{"name": "x", "anchor": DUP_ANCHOR,
                                "replace": "    if not text:\n        return -1\n"}], timeout=30)
    check("3.2 anchor with 2+ occurrences - SKIPPED, not applied, count named",
          r_dup["rows"][0]["outcome"] == mc.SKIPPED
          and "%d times" % DUP_COUNT in r_dup["rows"][0]["detail"], str(r_dup["rows"]))

    # ==================================================== 4. LINE ENDINGS - MEASURED, NOT ASSUMED
    tmp = tempfile.mkdtemp(prefix="mutate-check-crlf-")
    try:
        sut = os.path.join(tmp, "sut.py")
        test = os.path.join(tmp, "test_sut.py")
        with open(sut, "w", newline="") as fh:
            fh.write("def add(a, b):\r\n    total = a + b\r\n    return total\r\n")
        with open(test, "w", newline="") as fh:
            fh.write("import sys, os\r\n"
                      "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\r\n"
                      "import sut\r\n"
                      "ok = sut.add(2, 3) == 5\r\n"
                      "print('OK' if ok else 'FAIL')\r\n"
                      "sys.exit(0 if ok else 1)\r\n")
        # the anchor is written with a plain \n - as in JSON; the file is actually CRLF
        r_crlf = mc.run_mutations(sut, test,
                                   [{"name": "crlf",
                                     "anchor": "def add(a, b):\n    total = a + b\n    return total\n",
                                     "replace": "def add(a, b):\n    total = a + b + 1\n    return total\n"}],
                                   timeout=30)
        check("4.1 a multi-line LF anchor is found in a CRLF file by measurement (1, not 0/2)",
              r_crlf["rows"][0]["outcome"] == mc.COVERED, str(r_crlf["rows"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ==================================================== 5. BASELINE NOT GREEN - "NOT CHECKED"
    tmp2 = tempfile.mkdtemp(prefix="mutate-check-redbase-")
    try:
        sut2 = os.path.join(tmp2, "sut.py")
        test2 = os.path.join(tmp2, "test_sut.py")
        with open(sut2, "w", encoding="utf-8") as fh:
            fh.write("def f():\n    return 1\n")
        with open(test2, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.exit(1)  # the battery is red EVEN on the clean subject\n")
        r_red = mc.run_mutations(sut2, test2,
                                  [{"name": "noop", "anchor": "def f():\n", "replace": "def f():\n"}],
                                  timeout=15)
        check("5.1 red baseline -> baseline_ok=False, verdict_code=2 (not \"clean\")",
              (not r_red["baseline_ok"]) and r_red["verdict_code"] == 2, str(r_red))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # ==================================================== 6. CLI: THE EXIT CODE IS COMPUTED
    tmp3 = tempfile.mkdtemp(prefix="mutate-check-cli-")
    try:
        t_sut3, t_test3 = toy_copy(os.path.join(tmp3, "t"))
        only_covered = os.path.join(tmp3, "covered.json")
        with open(only_covered, "w", encoding="utf-8") as fh:
            json.dump([LIVE_MUTANTS[1]], fh)  # the COVERED mutant only
        p_ok = subprocess.run([sys.executable, MODULE, t_sut3, t_test3, only_covered],
                               capture_output=True, timeout=60)
        check("6.1 CLI: covered mutants only -> exit 0", p_ok.returncode == 0,
              "rc=%s" % p_ok.returncode)

        with_hole = os.path.join(tmp3, "hole.json")
        with open(with_hole, "w", encoding="utf-8") as fh:
            json.dump([LIVE_MUTANTS[0]], fh)  # the HOLE mutant only
        p_bad = subprocess.run([sys.executable, MODULE, t_sut3, t_test3, with_hole],
                                capture_output=True, timeout=60)
        check("6.2 CLI: a hole -> exit 1 (different inputs give different codes, not a literal)",
              p_bad.returncode == 1, "rc=%s" % p_bad.returncode)
        check("6.3 two runs of the same CLI gave DIFFERENT codes - so the code is computed",
              p_ok.returncode != p_bad.returncode)
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    # ============================== 7. OVERLAPPING ANCHOR OCCURRENCES (count_overlapping)
    # A multi-line anchor `x = 1\nx = 1\n` in a text of THREE such lines occurs twice with an
    # overlap; `str.count` would count 1 - and the mutation would land in the file silently.
    OVERLAP_SRC = "x = 1\nx = 1\nx = 1\n"
    OVERLAP_ANCHOR = "x = 1\nx = 1\n"
    check("7.1 count_overlapping counts OVERLAPPING occurrences (str.count would give 1)",
          mc.count_overlapping(OVERLAP_SRC, OVERLAP_ANCHOR) == 2,
          "got %d" % mc.count_overlapping(OVERLAP_SRC, OVERLAP_ANCHOR))

    tmp4 = tempfile.mkdtemp(prefix="mutate-check-overlap-")
    try:
        sut4 = os.path.join(tmp4, "sut.py")
        test4 = os.path.join(tmp4, "test_sut.py")
        with open(sut4, "w", encoding="utf-8", newline="") as fh:
            fh.write(OVERLAP_SRC)
        with open(test4, "w", encoding="utf-8", newline="") as fh:
            fh.write("import sys\nprint('OK')\nsys.exit(0)\n")
        r_ov = mc.run_mutations(sut4, test4,
                                 [{"name": "overlap", "anchor": OVERLAP_ANCHOR,
                                   "replace": "x = 2\nx = 2\n"}], timeout=30)
        row = r_ov["rows"][0]
        check("7.2 overlapping anchor -> SKIPPED with count 2, the mutation NOT applied",
              row["outcome"] == mc.SKIPPED and "2 times" in row["detail"], str(r_ov["rows"]))
    finally:
        shutil.rmtree(tmp4, ignore_errors=True)

    # ============================== 8. DURABLE RECORD: SAVE MODE
    # The main risk of the mode is collapsing the outcomes to two in serialization: the file
    # would look like a check and lie. The checks below hold exactly that.
    tmp5 = tempfile.mkdtemp(prefix="mutate-check-save-")
    try:
        check("8.0 default destination = <script stem>.mutations-result.json next to the script",
              mc.default_result_path(os.path.join(tmp5, "tool.py"))
              == os.path.join(tmp5, "tool" + mc.RESULT_SUFFIX),
              mc.default_result_path(os.path.join(tmp5, "tool.py")))

        # a corpus of the THREE live mutants gives three DIFFERENT outcomes - the file is checked on it
        all_json = os.path.join(tmp5, "all.json")
        with open(all_json, "w", encoding="utf-8") as fh:
            json.dump(LIVE_MUTANTS, fh, ensure_ascii=False)

        # --- 8.1 a normal run WITHOUT the flag: a check that must STAY SILENT
        t_dir5 = os.path.join(tmp5, "t")
        t_sut5, t_test5 = toy_copy(t_dir5)
        conv_path = mc.default_result_path(t_sut5)
        stamp_before = stamp(conv_path)
        before = set(os.listdir(t_dir5))
        p_plain = subprocess.run([sys.executable, MODULE, t_sut5, t_test5, all_json],
                                 capture_output=True, timeout=180)
        after = set(os.listdir(t_dir5))
        plain_out = (p_plain.stdout or b"").decode("utf-8", "replace")
        check("8.1 must stay silent: without the flag no file appears, exit and table unchanged",
              before == after
              and stamp(conv_path) == stamp_before
              and p_plain.returncode == 1
              and "Total: COVERED" in plain_out
              and "Result saved" not in plain_out,
              # both halves are needed: the count of new files catches an appearance HERE AND
              # NOW, the fingerprint catches an overwrite of a file that existed before the run
              "new files: %s; fingerprint %s -> %s; rc=%s"
              % (sorted(after - before), stamp_before, stamp(conv_path), p_plain.returncode))

        # --- 8.2 --out: the file appears, the JSON is valid, the verdict is the same
        out_file = os.path.join(tmp5, "res.json")
        p_save = subprocess.run([sys.executable, MODULE, t_sut5, t_test5, all_json,
                                 "--out", out_file], capture_output=True, timeout=180)
        check("8.2 --out: file created, exit code not changed by the fact of saving",
              os.path.isfile(out_file) and p_save.returncode == p_plain.returncode,
              "rc=%s vs %s" % (p_save.returncode, p_plain.returncode))

        payload = json.load(open(out_file, encoding="utf-8")) if os.path.isfile(out_file) else {}
        saved = {r["name"]: r["outcome"] for r in payload.get("rows", [])}

        # --- 8.3 THE main check that must turn red if the outcomes collapse
        check("8.3 the file holds THREE DIFFERENT outcomes by name - serialization did not collapse them",
              saved.get("HOLE-max-words-const") == mc.HOLE
              and saved.get("COVERED-unique-always-adds") == mc.COVERED
              and saved.get("CRASH-mean-guard-removed") == mc.CRASH
              and len(set(saved.values())) == 3,
              str(saved))
        check("8.4 the file holds the vocabulary of ALL five outcomes (\"not a HOLE\" is not \"COVERED\")",
              set(payload.get("outcomes_vocabulary", []))
              == {mc.COVERED, mc.HOLE, mc.CRASH, NOSTART, mc.SKIPPED},
              str(payload.get("outcomes_vocabulary")))
        check("8.5 the file total matches the sum of its own rows",
              sum(payload.get("counts", {}).values()) == len(payload.get("rows", []))
              and payload.get("counts", {}).get(mc.HOLE) == 1
              and payload.get("counts", {}).get(mc.COVERED) == 1,
              str(payload.get("counts")))
        check("8.6 the file is self-contained: schema, date, subject, battery, corpus, verdict",
              payload.get("schema") == mc.SCHEMA
              and bool(payload.get("generated_utc"))
              and os.path.basename(payload.get("script", "")) == "textstats.py"
              and os.path.basename(payload.get("battery", "")) == "test_textstats.py"
              and os.path.basename(payload.get("mutations_file", "")) == "all.json"
              and payload.get("verdict_code") == 1
              and payload.get("baseline_ok") is True,
              str({k: payload.get(k) for k in ("schema", "verdict_code", "baseline_ok")}))

        # --- 8.7 a write failure does not pass silently
        bad_out = os.path.join(tmp5, "no-such-folder", "res.json")
        p_bad = subprocess.run([sys.executable, MODULE, t_sut5, t_test5, all_json,
                                "--out", bad_out], capture_output=True, timeout=180)
        bad_txt = (p_bad.stdout or b"").decode("utf-8", "replace")
        check("8.7 the write failed -> exit 2 and an explicit \"NOT saved\", not a silent loss",
              p_bad.returncode == 2 and "NOT saved" in bad_txt,
              "rc=%s; %s" % (p_bad.returncode, bad_txt[-160:]))

        # --- 8.8 --save without a path puts the file exactly by convention (on a temporary subject)
        sut5 = os.path.join(tmp5, "sut.py")
        test5 = os.path.join(tmp5, "test_sut.py")
        with open(sut5, "w", encoding="utf-8", newline="") as fh:
            fh.write("def add(a, b):\n    return a + b\n")
        with open(test5, "w", encoding="utf-8", newline="") as fh:
            fh.write("import sys, os\n"
                     "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                     "import sut\nsys.exit(0 if sut.add(2, 3) == 5 else 1)\n")
        mut5 = os.path.join(tmp5, "m.json")
        with open(mut5, "w", encoding="utf-8") as fh:
            json.dump([{"name": "add-broken", "anchor": "    return a + b\n",
                        "replace": "    return a + b + 1\n"}], fh, ensure_ascii=False)
        p_def = subprocess.run([sys.executable, MODULE, sut5, test5, mut5, "--save"],
                               capture_output=True, timeout=120)
        check("8.8 --save without a path -> the file exactly by convention, exit 0 (all covered)",
              os.path.isfile(mc.default_result_path(sut5)) and p_def.returncode == 0,
              "rc=%s, file exists: %s" % (p_def.returncode,
                                          os.path.isfile(mc.default_result_path(sut5))))
    finally:
        shutil.rmtree(tmp5, ignore_errors=True)

    # ============================== 9. BYTECODE CACHE
    # The battery process must get PYTHONDONTWRITEBYTECODE=1 regardless of the caller's
    # environment; otherwise the next mutant of THE SAME byte length may run on stale bytecode.
    tmp9 = tempfile.mkdtemp(prefix="mutate-check-f1-")
    try:
        test9 = os.path.join(tmp9, "test_env.py")
        with open(test9, "w", encoding="utf-8", newline="") as fh:
            fh.write("import os, sys\n"
                     "print('ENV=' + repr(os.environ.get('PYTHONDONTWRITEBYTECODE')))\n"
                     "sys.exit(0)\n")
        old_val9 = os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        try:
            rc9, out9, err9 = mc.run_battery(test9, cwd=tmp9, timeout=30)
        finally:
            if old_val9 is not None:
                os.environ["PYTHONDONTWRITEBYTECODE"] = old_val9
        check("9.1 PYTHONDONTWRITEBYTECODE=1 is forced in the battery child process "
              "REGARDLESS of the caller's environment (even when it is absent there)",
              "ENV='1'" in out9, out9)
    finally:
        shutil.rmtree(tmp9, ignore_errors=True)

    # 9.2 - the GRANDCHILD: the battery itself starts a subprocess - the variable must reach it.
    # This tells the chosen way (an environment variable) from the `-B` flag, which a spawned
    # interpreter does not get.
    tmp10 = tempfile.mkdtemp(prefix="mutate-check-f1-grandchild-")
    try:
        test10 = os.path.join(tmp10, "test_grandchild.py")
        with open(test10, "w", encoding="utf-8", newline="") as fh:
            fh.write(
                "import os, sys, subprocess\n"
                "code = \"import os; print('GRANDCHILD_ENV=' + "
                "repr(os.environ.get('PYTHONDONTWRITEBYTECODE')))\"\n"
                "p = subprocess.run([sys.executable, '-c', code], capture_output=True)\n"
                "sys.stdout.write(p.stdout.decode('utf-8', 'replace'))\n"
                "sys.exit(0)\n")
        old_val10 = os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        try:
            rc10, out10, err10 = mc.run_battery(test10, cwd=tmp10, timeout=30)
        finally:
            if old_val10 is not None:
                os.environ["PYTHONDONTWRITEBYTECODE"] = old_val10
        check("9.2 the variable reaches a process started BY THE BATTERY ITSELF "
              "(inheritance through env=None, not the -B flag, which would not reach a grandchild)",
              "GRANDCHILD_ENV='1'" in out10, out10)
    finally:
        shutil.rmtree(tmp10, ignore_errors=True)

    # 9.3 - regression on the class itself: two mutants of THE SAME length in a row, each must
    # get ITS OWN verdict, not the neighbour's. The class depends on mtime resolution, so this
    # is a measurement, not a guarantee.
    tmp11 = tempfile.mkdtemp(prefix="mutate-check-f1-samelen-")
    try:
        sut11 = os.path.join(tmp11, "sut.py")
        test11 = os.path.join(tmp11, "test_sut.py")
        with open(sut11, "w", encoding="utf-8", newline="") as fh:
            fh.write('def value():\n    return "AAAA"\n')
        with open(test11, "w", encoding="utf-8", newline="") as fh:
            fh.write(
                "import sys, os\n"
                "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                "import sut\n"
                "v = sut.value()\n"
                "if v == 'BBBB':\n"
                "    print('[FAIL] case-B')\n"
                "    sys.exit(1)\n"
                "elif v == 'CCCC':\n"
                "    print('[FAIL] case-C')\n"
                "    sys.exit(1)\n"
                "else:\n"
                "    sys.exit(0)\n")
        mut11 = [
            {"name": "mut-B", "anchor": '    return "AAAA"\n', "replace": '    return "BBBB"\n'},
            {"name": "mut-C", "anchor": '    return "AAAA"\n', "replace": '    return "CCCC"\n'},
        ]
        old_val11 = os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        try:
            res11 = mc.run_mutations(sut11, test11, mut11, timeout=30)
        finally:
            if old_val11 is not None:
                os.environ["PYTHONDONTWRITEBYTECODE"] = old_val11
        by_name11 = {r["name"]: r for r in res11["rows"]}
        check("9.3 two mutants of THE SAME length in a row - each gets ITS OWN verdict, "
              "not the neighbour's, even without the variable in the caller's environment",
              by_name11.get("mut-B", {}).get("cases") == ["case-B"]
              and by_name11.get("mut-C", {}).get("cases") == ["case-C"],
              str({k: v.get("cases") for k, v in by_name11.items()}))
    finally:
        shutil.rmtree(tmp11, ignore_errors=True)

    # ============================== 10. THE BATTERY DID NOT START
    # `sys.exit(2)` on import of the subject gives SystemExit WITHOUT a traceback, and a
    # non-zero code would read as a catch. Check 10.2 deliberately catches a mutation with THE
    # SAME code 2 and in a `MISS` convention, not `[FAIL]`: the distinguishing sign is "the
    # battery printed at least a line", not the exit code and not the check name.
    tmp12 = tempfile.mkdtemp(prefix="mutate-check-nostart-")
    try:
        sut12 = os.path.join(tmp12, "sut.py")
        test12 = os.path.join(tmp12, "test_sut.py")
        with open(sut12, "w", encoding="utf-8", newline="") as fh:
            fh.write("import sys\nREADY = True\n\n\ndef add(a, b):\n    return a + b\n")
        with open(test12, "w", encoding="utf-8", newline="") as fh:
            fh.write("import sys, os\n"
                     "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                     "import sut\n"
                     "ok = sut.add(2, 3) == 5\n"
                     "print('HIT add' if ok else 'MISS add')\n"
                     "sys.exit(0 if ok else 2)\n")
        mut12 = [
            {"name": "nostart-exit-on-import", "anchor": "READY = True\n",
             "replace": "sys.exit(2)\n"},
            {"name": "caught-by-point-rc2", "anchor": "    return a + b\n",
             "replace": "    return a - b\n"},
            {"name": "nostart-stderr-then-exit", "anchor": "READY = True\n",
             "replace": "sys.stderr.write('subject did not find its root\\n'); sys.exit(2)\n"},
        ]
        res12 = mc.run_mutations(sut12, test12, mut12, timeout=30)
        by12 = {r["name"]: r for r in res12["rows"]}
        check("10.1 must hit: sys.exit(2) on import without a traceback "
              "-> NOT COUNTED (no start), not COVERED",
              by12.get("nostart-exit-on-import", {}).get("outcome") == NOSTART,
              str(by12.get("nostart-exit-on-import")))
        check("10.2 must miss: a battery check caught the mutation with the same "
              "code 2 and in a `MISS` convention (not `[FAIL]`) -> still COVERED",
              by12.get("caught-by-point-rc2", {}).get("outcome") == mc.COVERED,
              str(by12.get("caught-by-point-rc2")))
        check("10.3 the subject wrote to stderr and exited on import - battery stdout empty -> "
              "no start; the stderr line is named in detail",
              by12.get("nostart-stderr-then-exit", {}).get("outcome") == NOSTART
              and "did not find its root" in by12.get("nostart-stderr-then-exit", {}).get("detail", ""),
              str(by12.get("nostart-stderr-then-exit")))
        check("10.4 the outcome counter matches the rows and verdict_code=1 (not \"all covered\")",
              res12["counts"].get(NOSTART) == 2 and res12["counts"].get(mc.COVERED) == 1
              and sum(res12["counts"].values()) == len(res12["rows"])
              and res12["verdict_code"] == 1,
              "%s, verdict=%s" % (res12["counts"], res12["verdict_code"]))

        only_nostart = os.path.join(tmp12, "nostart.json")
        with open(only_nostart, "w", encoding="utf-8") as fh:
            json.dump([mut12[0]], fh, ensure_ascii=False)
        p12 = subprocess.run([sys.executable, MODULE, sut12, test12, only_nostart],
                             capture_output=True, timeout=60)
        out12 = (p12.stdout or b"").decode("utf-8", "replace")
        check("10.5 CLI: a single \"no start\" mutant -> exit 1 and the summary line "
              "names it separately, not as zero coverage",
              p12.returncode == 1 and "no start 1" in out12,
              "rc=%s; %s" % (p12.returncode, out12[-200:]))

        # 10.6 - a battery SILENT on the baseline too (prints nothing, speaks only by exit code):
        # the "no start" sign does not apply to it, otherwise each honest catch of it would become
        # "no start" - a false alarm. Must miss.
        test12s = os.path.join(tmp12, "test_silent.py")
        with open(test12s, "w", encoding="utf-8", newline="") as fh:
            fh.write("import sys, os\n"
                     "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                     "import sut\n"
                     "sys.exit(0 if sut.add(2, 3) == 5 else 1)\n")
        res12s = mc.run_mutations(sut12, test12s, [mut12[1]], timeout=30)
        row12s = res12s["rows"][0] if res12s["rows"] else {}
        check("10.6 must miss: a battery silent on the baseline caught the mutation "
              "-> COVERED with the \"silent\" note, not \"no start\"",
              row12s.get("outcome") == mc.COVERED and "silent" in row12s.get("detail", ""),
              str(row12s))

        # 10.9 - the "silent on baseline" flag must reach the durable file, otherwise COVERED of a
        # silent battery looks the same as COVERED of a speaking one. Both sides at once: silent ->
        # False, speaking -> True; a missing field (None) turns both red.
        pay_mute = mc.build_report(res12s, sut12, test12s, None, 30)
        pay_talk = mc.build_report(res12, sut12, test12, None, 30)
        check("10.9 the durable file carries baseline_speaks: silent battery -> False, "
              "speaking -> True",
              pay_mute.get("baseline_speaks") is False and pay_talk.get("baseline_speaks") is True,
              "silent=%r, speaking=%r" % (pay_mute.get("baseline_speaks"),
                                          pay_talk.get("baseline_speaks")))

        # 10.7 / 10.8 - for a battery silent on the baseline, death on import stays COVERED (limit
        # (2) of the header); the summary carries a separate warning, the exit code does not change.
        WARN_MUTE = "Battery silent on baseline too"
        p12m = subprocess.run([sys.executable, MODULE, sut12, test12s, only_nostart],
                              capture_output=True, timeout=60)
        out12m = (p12m.stdout or b"").decode("utf-8", "replace")
        total12m = [ln for ln in out12m.splitlines() if ln.startswith("Total:")]
        check("10.7 must hit: a battery silent on the baseline + sys.exit on import "
              "-> the summary carries \"Battery silent on baseline too\", the exit code is the same (0)",
              WARN_MUTE in out12m and p12m.returncode == 0,
              "rc=%s; %s; warning=%s" % (p12m.returncode, total12m[:1], WARN_MUTE in out12m))

        caught_only = os.path.join(tmp12, "caught.json")
        with open(caught_only, "w", encoding="utf-8") as fh:
            json.dump([mut12[1]], fh, ensure_ascii=False)
        p12c = subprocess.run([sys.executable, MODULE, sut12, test12, caught_only],
                              capture_output=True, timeout=60)
        out12c = (p12c.stdout or b"").decode("utf-8", "replace")
        check("10.8 must miss: a battery speaking on the baseline honestly caught the "
              "mutation -> no \"silent\" warning in the summary, code 0",
              WARN_MUTE not in out12c and p12c.returncode == 0 and "COVERED 1" in out12c,
              "rc=%s; %s" % (p12c.returncode, out12c[-300:]))
    finally:
        shutil.rmtree(tmp12, ignore_errors=True)

    # 11 - `--help` is help with code 0 and no mutation run; an unknown call form is still
    # "usage" with code 2 (a check that must miss).
    p11h = subprocess.run([sys.executable, MODULE, "--help"], capture_output=True, timeout=60)
    out11h = (p11h.stdout or b"").decode("utf-8", "replace")
    check("11.1 `--help` -> code 0, help text, no mutations run",
          p11h.returncode == 0 and "mutate_check.py" in out11h and "Total:" not in out11h,
          "rc=%s; %s" % (p11h.returncode, out11h[:200]))
    p11x = subprocess.run([sys.executable, MODULE, "-x"], capture_output=True, timeout=60)
    check("11.2 must miss: `-x` (not help) -> code 2 as before",
          p11x.returncode == 2, "rc=%s" % p11x.returncode)

    # 12 - `--anchors-only`: anchor uniqueness without running the battery.
    check_anchors = getattr(mc, "check_anchors", None)
    mut12a = LIVE_MUTANTS + [
        {"name": "A-missing", "anchor": "no such line anywhere\n", "replace": "y\n"},
        {"name": "A-dup", "anchor": DUP_ANCHOR, "replace": "    if not text:\n        return -1\n"},
    ]
    ar = check_anchors(TOY, mut12a) if check_anchors else None
    bad12 = dict(ar["bad"]) if ar else {}
    check("12.1 must hit: anchors with 0 and with %d occurrences are named with their count, "
          "5 inspected" % DUP_COUNT,
          ar is not None and bad12 == {"A-missing": 0, "A-dup": DUP_COUNT} and ar["total"] == 5,
          "got: %r" % (ar,))
    check("12.2 must miss: the three live anchors (unique) are not among the violations",
          ar is not None and not ({m["name"] for m in LIVE_MUTANTS} & set(bad12)),
          "got: %r" % (ar,))

    tmp13 = tempfile.mkdtemp(prefix="mutate-check-anchors-")
    try:
        # a CRLF subject + a multi-line anchor with `\n`: the check must judge as the run does
        sut13 = os.path.join(tmp13, "sut.py")
        with open(sut13, "w", encoding="utf-8", newline="") as fh:
            fh.write("def f():\r\n    a = 1\r\n    return a\r\n")
        m13_ml = [{"name": "ml", "anchor": "    a = 1\n    return a\n", "replace": "    a = 2\n    return a\n"}]
        ar13 = check_anchors(sut13, m13_ml) if check_anchors else None
        _t, applied13, _c = mc.apply_mutation(open(sut13, encoding="utf-8", newline="").read(),
                                              m13_ml[0]["anchor"], m13_ml[0]["replace"])
        check("12.3 must miss: a multi-line anchor on a CRLF subject is unique - "
              "as in the run (apply_mutation applies it)",
              ar13 is not None and ar13["bad"] == [] and applied13,
              "anchors=%r, applied=%r" % (ar13, applied13))

        t_dir13 = os.path.join(tmp13, "t")
        t_sut13, _t_test13 = toy_copy(t_dir13)

        def cli13(muts, *extra):
            mp = os.path.join(tmp13, "m.json")
            with open(mp, "w", encoding="utf-8") as fh:
                json.dump(muts, fh, ensure_ascii=False)
            # the battery in position 2 does NOT exist: the mode must not touch it
            p = subprocess.run([sys.executable, MODULE, t_sut13,
                                os.path.join(tmp13, "no_such_test.py"), mp, "--anchors-only"]
                               + list(extra), capture_output=True, timeout=60)
            return p.returncode, (p.stdout or b"").decode("utf-8", "replace")

        rc_ok, out_ok = cli13(LIVE_MUTANTS)
        check("12.4 CLI `--anchors-only`, all anchors unique -> code 0, \"inspected 3 - "
              "non-unique 0\", the battery was not run (no run summary)",
              rc_ok == 0 and "mutants inspected 3" in out_ok
              and "non-unique anchors 0" in out_ok and "Total:" not in out_ok,
              "rc=%s; %s" % (rc_ok, out_ok[-300:]))
        rc_bad, out_bad = cli13(mut12a)
        check("12.5 CLI `--anchors-only`, two non-unique -> code 1, both named",
              rc_bad == 1 and "A-missing: occurrences 0" in out_bad
              and "A-dup: occurrences %d" % DUP_COUNT in out_bad,
              "rc=%s; %s" % (rc_bad, out_bad[-300:]))
        rc_dn, out_dn = cli13(LIVE_MUTANTS + [dict(LIVE_MUTANTS[1])])
        check("12.6 CLI `--anchors-only`, a mutant name twice -> code 1, the name is named",
              rc_dn == 1 and "duplicate name: COVERED-unique-always-adds" in out_dn,
              "rc=%s; %s" % (rc_dn, out_dn[-300:]))
        before13 = set(os.listdir(t_dir13))
        rc_sv, out_sv = cli13(LIVE_MUTANTS, "--save")
        check("12.7 `--anchors-only --save` -> code 2, no result file appeared next to the subject",
              rc_sv == 2 and "incompatible" in out_sv and set(os.listdir(t_dir13)) == before13,
              "rc=%s; new files=%s; %s" % (rc_sv, set(os.listdir(t_dir13)) - before13, out_sv[-200:]))
        # 12.8: the ban also holds the `--out` branch, not only `--save`
        out13 = os.path.join(tmp13, "res.json")
        rc_out, out_out = cli13(LIVE_MUTANTS, "--out", out13)
        check("12.8 `--anchors-only --out <file>` -> code 2, the named file did not appear",
              rc_out == 2 and "incompatible" in out_out and not os.path.exists(out13),
              "rc=%s; file exists=%s; %s" % (rc_out, os.path.exists(out13), out_out[-200:]))
    finally:
        shutil.rmtree(tmp13, ignore_errors=True)

    # 13 - `--only <names>`: a subset of the corpus by names without a subset file. `--save`
    # with `--only` is forbidden (the durable file attests the WHOLE corpus), `--out` is allowed -
    # the file then carries an `only` field.
    tmp14 = tempfile.mkdtemp(prefix="mutate-check-only-")
    try:
        mp14 = os.path.join(tmp14, "m.json")
        with open(mp14, "w", encoding="utf-8") as fh:
            json.dump(LIVE_MUTANTS, fh, ensure_ascii=False)
        n_hole, n_cov, n_crash = (m["name"] for m in LIVE_MUTANTS)
        # the subject is a COPY in a temporary folder: a broken guard writes the durable file next
        # to the subject, and on the real example that would litter the repository
        sut14, test14 = toy_copy(os.path.join(tmp14, "t"))

        def cli14(*extra, battery=None):
            p = subprocess.run([sys.executable, MODULE, sut14, battery or os.path.join(tmp14, "no_such_test.py"),
                                mp14] + list(extra), capture_output=True, timeout=600)
            return p.returncode, (p.stdout or b"").decode("utf-8", "replace")

        rc, out = cli14("--anchors-only", "--only", n_cov)
        check("13.1 `--only <one name> --anchors-only` -> code 0, 1 mutant inspected, \"subset 1 of 3\"",
              rc == 0 and "mutants inspected 1 " in out and "Subset --only: 1 of 3" in out,
              "rc=%s; %s" % (rc, out[-300:]))
        rc, out = cli14("--anchors-only", "--only", "%s,%s" % (n_crash, n_hole))
        check("13.2 `--only` with two names -> 2 inspected (must miss: the third is not taken)",
              rc == 0 and "mutants inspected 2 " in out and "Subset --only: 2 of 3" in out,
              "rc=%s; %s" % (rc, out[-300:]))
        rc, out = cli14("--anchors-only", "--only", "%s,NO-SUCH,NOR-THIS" % n_cov)
        check("13.3 unknown names -> code 2, both unknown named, the known one not listed as unknown",
              rc == 2 and "NO-SUCH" in out and "NOR-THIS" in out
              and ("names not in the corpus: NO-SUCH, NOR-THIS" in out),
              "rc=%s; %s" % (rc, out[-300:]))
        for tag, val in (("empty string", ""), ("commas only", " , ,")):
            rc, out = cli14("--anchors-only", "--only", val)
            check("13.4 `--only` %s -> code 2 \"empty list\"" % tag, rc == 2 and "empty list" in out,
                  "rc=%s; %s" % (rc, out[-200:]))
        for tag, args in (("last", ("--anchors-only", "--only")), ("before another flag", ("--only", "--anchors-only"))):
            rc, out = cli14(*args)
            check("13.5 `--only` without a value (%s) -> code 2 \"requires a comma-separated list\"" % tag,
                  rc == 2 and "requires a comma-separated list" in out, "rc=%s; %s" % (rc, out[-200:]))
        rc, out = cli14("--anchors-only", "--only", "NO-SUCH,NO-SUCH")
        check("13.10 a repeated unknown name collapses: named once",
              rc == 2 and out.count("NO-SUCH") == 1, "rc=%s; %s" % (rc, out[-200:]))
        conv14 = mc.default_result_path(TOY)
        st_before = stamp(conv14)
        t_dir14 = os.path.dirname(sut14)
        before14 = set(os.listdir(t_dir14))
        p14 = subprocess.run([sys.executable, MODULE, sut14, os.path.join(tmp14, "no_such_test.py"), mp14,
                              "--only", n_cov, "--save"], capture_output=True, timeout=600)
        rc, out = p14.returncode, (p14.stdout or b"").decode("utf-8", "replace")
        check("13.6 `--only` + `--save` -> code 2 \"incompatible\", no result file next to the subject",
              rc == 2 and "incompatible" in out and not os.path.exists(mc.default_result_path(sut14))
              and set(os.listdir(t_dir14)) == before14,
              "rc=%s; %s" % (rc, out[-300:]))
        # and a run - on the copy of the subject with its battery: a mutant that loses --out writes next to the COPY
        out14 = os.path.join(tmp14, "res.json")
        p14 = subprocess.run([sys.executable, MODULE, sut14, test14, mp14,
                              "--only", n_cov, "--out", out14], capture_output=True, timeout=600)
        rc, out = p14.returncode, (p14.stdout or b"").decode("utf-8", "replace")
        pay14 = json.load(open(out14, encoding="utf-8")) if os.path.isfile(out14) else {}
        check("13.7 `--only <name> --out <file>` -> ONE mutant run, COVERED, the file has `only` = [name] and 1 row",
              rc == 0 and pay14.get("only") == [n_cov] and [r["name"] for r in pay14.get("rows", [])] == [n_cov]
              and pay14["rows"][0]["outcome"] == mc.COVERED and stamp(conv14) == st_before
              and not os.path.exists(mc.default_result_path(sut14)),
              "rc=%s; only=%r; rows=%r; %s" % (rc, pay14.get("only"), [r.get("name") for r in pay14.get("rows", [])],
                                              out[-300:]))
        rc, out = cli14("--anchors-only", "--only", "%s,%s,%s" % (n_crash, n_hole, n_crash))
        check("13.8 a repeated own name in `--only` does not double the selection: 2 inspected",
              rc == 0 and "mutants inspected 2 " in out, "rc=%s; %s" % (rc, out[-300:]))
        check("13.9 must miss: without `only` the field is in the file and equals None",
              "only" in mc.build_report({"rows": []}, TOY, TOY_TEST, None, 60)
              and mc.build_report({"rows": []}, TOY, TOY_TEST, None, 60)["only"] is None,
              "got: %r" % (mc.build_report({"rows": []}, TOY, TOY_TEST, None, 60).get("only", "NO FIELD"),))

        # 14 - `--timeout` without a value or not an integer -> code 2, no traceback. Through
        # `--anchors-only` without a battery: flag parsing is checked, the battery is not run. The
        # traceback would go to stderr, so this call reads BOTH streams.
        def cli14e(*extra):
            p = subprocess.run([sys.executable, MODULE, sut14, os.path.join(tmp14, "no_such_test.py"),
                                mp14] + list(extra), capture_output=True, timeout=60)
            return (p.returncode, (p.stdout or b"").decode("utf-8", "replace"),
                    (p.stderr or b"").decode("utf-8", "replace"))

        for tag, args in (("last", ("--anchors-only", "--timeout")),
                          ("before another flag", ("--timeout", "--foo", "--anchors-only")),
                          ("non-integer \"x\"", ("--anchors-only", "--timeout", "x")),
                          ("fractional \"1.5\"", ("--anchors-only", "--timeout", "1.5")),
                          ("superscript two", ("--anchors-only", "--timeout", "²"))):
            rc, out, err = cli14e(*args)
            check("14.1 `--timeout` %s -> code 2 \"requires a whole number\", no traceback" % tag,
                  rc == 2 and "requires a whole number" in out and "Traceback" not in out + err,
                  "rc=%s; %s | %s" % (rc, out[-200:], err[-200:]))
        rc, out = cli14("--anchors-only", "--timeout", "30")
        check("14.2 must miss: `--timeout 30` accepted -> code 0, 3 inspected",
              rc == 0 and "mutants inspected 3 " in out, "rc=%s; %s" % (rc, out[-200:]))
    finally:
        shutil.rmtree(tmp14, ignore_errors=True)

    total = len(PASS) + len(FAIL)
    print("\nTOTAL: %d/%d · checks inspected %d" % (len(PASS), total, total))
    if total == 0:
        # zero inspected checks is "could not check", not "clean"
        print("COULD NOT CHECK: 0 checks inspected - the battery executed no check")
        return 2
    if FAIL:
        print("FAILED CHECKS: %s" % ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
