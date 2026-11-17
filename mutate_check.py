#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_check.py - prove that a test battery can catch a defect at all.

WHY. A green battery says nothing about whether its checks can turn red. The tool copies the
script and its battery into a temporary folder, breaks one line of the COPY (a "mutant"), runs
the battery, restores the line, and moves on to the next mutant. A battery that stays green on
a broken script has a hole.

THE MAIN REQUIREMENT. The naive verdict "nothing turned red = hole" gives FALSE holes:
- an anchor that "was not found" is often a line-ending mismatch (the file is CRLF, the anchor
  is written with LF);
- a battery that CRASHED on an unhandled exception did not stay silent - it is neither a hole
  nor coverage, it is an outcome of its own;
- a mutant that turned an OLD test red (one that a "list of new tests" filter does not look at)
  is covered - just under another test name.

Hence five distinguishable outcomes per mutant:
  COVERED      - the battery exit code became non-zero WITHOUT a traceback, stdout is not
                 empty: the defect was caught. Any red test counts, old or new; the tool
                 does not filter by a "list of new tests".
  NOT COUNTED (crash)
               - a `Traceback` was found in the battery stderr (an unhandled exception took
                 down the battery process itself, not one of its deliberate checks), or the
                 battery hit the timeout. Neither a hole nor coverage.
  NOT COUNTED (no start)
               - non-zero exit, no `Traceback`, and the battery stdout is EMPTY (not a single
                 non-whitespace character) while the same battery prints something on the
                 baseline. A battery that speaks on the clean subject and prints nothing here
                 executed no check at all: the typical case is a mutation like `sys.exit(2)`
                 in module-level code - the battery dies importing the subject, and
                 `SystemExit` prints no traceback.
                 The sign is the presence of output at all, NOT the names of checks
                 (`[FAIL]` and the like): conventions differ between batteries.
                 Limits of the sign, named in advance:
                 (1) a subject that manages to PRINT to stdout before dying on import is not
                     caught by this sign;
                 (2) a battery that is SILENT already on the baseline (empty stdout on the
                     clean subject) is beyond the sign: its start cannot be told from its
                     output, its non-zero exit stays COVERED, and `detail` carries the note
                     "battery silent on baseline too - start not verified by output";
                 (3) a battery that printed some checks and was then killed by the subject
                     (`sys.exit` inside a function the battery calls without a guard) stays
                     COVERED, although death stopped it, not a check;
                 (4) the reverse false alarm: a battery that speaks on the baseline but stays
                     silent in stdout when it catches a defect (result only via exit code or
                     stderr; output lost in a buffer on `os._exit`) lands here with the wrong
                     detail "no check executed".
                 This outcome gives exit code 1, like the crash.
  HOLE         - the battery exit code stayed zero (as on the baseline): nobody noticed.
  SKIPPED (anchor)
               - the mutation anchor is not unique (0 or 2+ occurrences) in the copy - the
                 mutation was not applied at all, there is nothing to restore; this is "not
                 checked", NOT "clean".

LINE ENDINGS - MEASURED, NOT ASSUMED. The anchor is searched in BOTH forms (LF and CRLF); the
form that gives EXACTLY one match in the actual bytes of the copy is used. Occurrences are
counted with overlaps (a shift of one character), so a multi-line anchor that overlaps itself
is not declared unique by mistake.

ROLLBACK. Work happens on a COPY of the script folder in a temporary directory (the script,
the battery and everything next to them - fixtures, imported modules). The original is never
opened for writing. Between mutants the broken file in the copy is overwritten with the clean
content (guaranteed, in `finally`); at the end the whole temporary folder is removed. The
battery runs with PYTHONDONTWRITEBYTECODE=1 forced into its environment, so a mutant of the
same byte length as the previous one cannot be judged on stale bytecode.

MUTATION FORMAT (JSON file, a list of objects):
  [{"name": "always-true", "anchor": "    if quote in text:\\n",
    "replace": "    if True:\\n"}, ...]
`anchor`/`replace` use `\\n` inside the string; the actual line ending is chosen by measurement.

WHERE THE RESULT GOES. By default the result is printed only. `--save` writes it next to the
subject script, named after its stem:
    input:   <stem>.mutations.json
    result:  <stem>.mutations-result.json
One file, overwritten by every run - history is kept by version control.

Usage:
  python mutate_check.py <script.py> <test_script.py> <mutations.json> [--timeout N]
                         [--save | --out <path.json>] [--anchors-only] [--only <name1,name2>]

  The script and its battery must lie in the same folder: that folder is what gets copied.
  --timeout  seconds per battery run (default 60).
  --save  save the result next to the subject script (see above).
  --out   the same, into an explicitly named file (implies saving).
  --anchors-only  check only that the anchors are unique (the same check that yields SKIPPED),
          without running the battery and without writing files; seconds instead of hours. The
          battery in position 2 is not started. Incompatible with --save/--out (code 2). Codes:
          0 - all anchors unique and no name repeats - 1 - violations (named one by one) - 2 - as below.
  --only  only the mutants with these names (comma-separated; repeats collapse, order as in the
          corpus), without a subset file. An unknown name or an empty list - code 2, unknown names
          listed. Incompatible with --save (code 2): the file next to the subject attests the WHOLE
          corpus; a subset result goes through --out, and the file carries an `only` field with
          the names. Works with --anchors-only too.

EXIT CODES: 0 - all mutants COVERED - 1 - there is a HOLE, a NOT COUNTED (either kind) or a
SKIPPED - 2 - could not check (baseline battery not green, files not found, broken JSON, OR the
result could not be written: a silent loss of the evidence is not allowed).

SAVING MUST NOT COLLAPSE THE OUTCOMES TO TWO. The file carries the same literals as the table,
plus an `outcomes_vocabulary` list with all five (schema `mutate-check/2`), so that a reader of
the file - a person or a script - sees that "not a HOLE" is not "COVERED". A binary verdict in
the file would be exactly the defect this tool was written against.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TRACEBACK_MARK = "Traceback (most recent call last)"

# The literals are fixed, but WHICH outcome a mutant gets is always COMPUTED (see run_mutations).
COVERED, CRASH, HOLE, SKIPPED = "COVERED", "NOT COUNTED (crash)", "HOLE", "SKIPPED (anchor)"
# Non-zero exit without a traceback and with an EMPTY stdout: the battery executed no check.
NOSTART = "NOT COUNTED (no start)"


def die(msg):
    print("ERROR: %s" % msg)
    sys.exit(2)


# --------------------------------------------------------------------- line endings, measured

def eol_variants(s):
    """(LF form, CRLF form) of a string - both, to be measured against the actual bytes."""
    lf = s.replace("\r\n", "\n")
    return lf, lf.replace("\n", "\r\n")


def count_overlapping(haystack, needle):
    """Count occurrences INCLUDING OVERLAPPING ones - shifting by ONE character.

    `str.count` counts only non-overlapping ones: `'\\n\\n\\n'.count('\\n\\n') == 1`, while
    there are two occurrences. For a uniqueness guard that is a lie in exactly the dangerous
    direction: the anchor is declared unique, the mutation is applied to the first
    occurrence, and the run proves something other than what is claimed. Multi-line anchors
    (the usual kind - blocks of code are edited with them) fall into this class most often."""
    if not needle:
        return 0
    n = 0
    pos = haystack.find(needle)
    while pos != -1:
        n += 1
        pos = haystack.find(needle, pos + 1)
    return n


def find_unique_form(haystack, needle_lf):
    """Look for needle (or its CRLF form) in haystack. Returns (form, count), where form is
    the one that gave EXACTLY one match; None if the SUM over the forms is 0 or 2+ (the
    forms cannot overlap: the LF form is never a substring of the CRLF form and vice versa,
    unless the needle is a single line - then both forms are equal, which is fine)."""
    lf, crlf = eol_variants(needle_lf)
    if lf == crlf:                       # single-line anchor - line endings do not matter
        n = count_overlapping(haystack, lf)
        return (lf, n) if n == 1 else (None, n)
    n_lf, n_crlf = count_overlapping(haystack, lf), count_overlapping(haystack, crlf)
    total = n_lf + n_crlf
    if n_lf == 1 and n_crlf == 0:
        return lf, 1
    if n_crlf == 1 and n_lf == 0:
        return crlf, 1
    return None, total


def apply_mutation(src_text, anchor, replace):
    """Returns (new_text, applied: bool, match_count).
    Applies nothing if the anchor is not unique - the guard comes BEFORE the write."""
    form, count = find_unique_form(src_text, anchor)
    if form is None:
        return src_text, False, count
    # bring the replacement to THE SAME line-ending form as the anchor that was found
    lf_repl, crlf_repl = eol_variants(replace)
    repl_form = crlf_repl if form.endswith("\r\n") or "\r\n" in form else lf_repl
    return src_text.replace(form, repl_form, 1), True, count


def check_anchors(script_path, mutations):
    """The `--anchors-only` mode: anchor uniqueness over the corpus WITHOUT running the battery.

    Judged by THE SAME `find_unique_form` as `apply_mutation` in a real run, over the subject
    bytes read the same way (`newline=""`) - so "not unique" here means exactly the SKIPPED
    outcome there, LF/CRLF included.
    Returns a dict: total, bad [(name, occurrences)], dup_names [name]."""
    with open(script_path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    bad = []
    for m in mutations:
        form, count = find_unique_form(text, m["anchor"])
        if form is None:
            bad.append((m["name"], count))
    names = [m["name"] for m in mutations]
    dup_names = sorted({n for n in names if names.count(n) > 1})
    return {"total": len(mutations), "bad": bad, "dup_names": dup_names}


# --------------------------------------------------------------------------------- workspace

def make_workspace(script_path, test_path):
    script_path = os.path.abspath(script_path)
    test_path = os.path.abspath(test_path)
    if not os.path.isfile(script_path):
        die("script not found: %s" % script_path)
    if not os.path.isfile(test_path):
        die("battery not found: %s" % test_path)
    sdir, tdir = os.path.dirname(script_path), os.path.dirname(test_path)
    if sdir != tdir:
        die("script and battery must lie in the same folder (otherwise it is unclear what to "
            "copy as fixtures): %s vs %s" % (sdir, tdir))
    tmp = tempfile.mkdtemp(prefix="mutate-check-")
    ws = os.path.join(tmp, "ws")
    shutil.copytree(sdir, ws, ignore=shutil.ignore_patterns("__pycache__", ".git", "*.pyc"))
    return tmp, ws, os.path.join(ws, os.path.basename(script_path)), os.path.join(ws, os.path.basename(test_path))


def run_battery(test_path, cwd, timeout):
    """Run the battery in a SEPARATE process - with the bytecode cache forcibly disabled.
    Between mutants the same subject file in the WORKING COPY is rewritten (see
    run_mutations); if a mutant file has the same byte size as the previous one (a common
    case: `True` -> `False`, one identifier swapped), the interpreter may treat a `.pyc`
    already lying in `__pycache__` as still valid and run the battery on OLD bytecode - the
    verdict for one mutant would then go to another.

    The `-B` flag would cover only this process (the child): `sys.executable` gets it, but an
    interpreter the child itself starts through `subprocess.run` (a grandchild) does NOT. The
    PYTHONDONTWRITEBYTECODE environment variable is inherited by the child, and by the
    GRANDCHILD as long as the battery passes `env=None` or a copy of `os.environ`; a battery
    that replaces `env` wholesale will NOT pass it on. So the variable is forced here, on top
    of a copy of the current environment (not instead of it - the child needs PATH and the
    rest).

    This closes both the WRITING and the READING of a stale cache: the workspace copy is made
    WITHOUT `__pycache__` (`make_workspace` does not copy it), and if no battery process in
    the workspace ever writes a `.pyc`, there is nothing stale to read."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        p = subprocess.run(
            [sys.executable, test_path], cwd=cwd, capture_output=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT (%ss)" % timeout
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace"), (p.stderr or b"").decode("utf-8", "replace")


def extract_red_cases(stdout):
    """Best effort: names of the red tests by TWO output conventions (`[FAIL] name` and
    `name ... FAILED`). Not an authoritative signal - the exit code plus the absence of a
    traceback are authoritative; this is only for a readable table."""
    import re
    names = []
    for m in re.finditer(r"^\[FAIL\]\s+(\S.*?)(?:\s{2,}<-.*)?$", stdout, re.M):
        names.append(m.group(1).strip())
    for m in re.finditer(r"^(\S[^\r\n]{0,80}?)\s{1,4}(?:FAILED)\b", stdout, re.M):
        cand = m.group(1).strip()
        if cand and cand not in names:
            names.append(cand)
    return names


# --------------------------------------------------------------------------------- the core

def run_mutations(script_path, test_path, mutations, timeout=60):
    """Returns a dict: baseline_rc, baseline_ok, rows (list), counts (dict), verdict_code (int)."""
    tmp, ws, ws_script, ws_test = make_workspace(script_path, test_path)
    try:
        with open(ws_script, "r", encoding="utf-8", newline="") as fh:
            clean_text = fh.read()

        base_rc, base_out, base_err = run_battery(ws_test, cwd=ws, timeout=timeout)
        baseline_ok = (base_rc == 0) and (TRACEBACK_MARK not in base_err)
        if not baseline_ok:
            return {
                "baseline_rc": base_rc, "baseline_ok": False, "rows": [],
                "counts": {}, "verdict_code": 2,
                "baseline_note": "battery is NOT green on the original (unmutated) script "
                                  "(rc=%s) - the mutation check does not apply; fix the "
                                  "subject or the battery first" % base_rc,
            }

        # "no start" applies only if the battery SPEAKS on the clean subject: for a silent
        # battery an empty stdout of a mutant says nothing about its start.
        baseline_speaks = bool(base_out.strip())
        rows = []
        counts = {COVERED: 0, CRASH: 0, NOSTART: 0, HOLE: 0, SKIPPED: 0}
        for mut in mutations:
            name = mut["name"]
            anchor, replace = mut["anchor"], mut["replace"]
            new_text, applied, count = apply_mutation(clean_text, anchor, replace)
            if not applied:
                rows.append({"name": name, "outcome": SKIPPED, "cases": [],
                             "detail": "anchor occurs %d times (need 1)" % count})
                counts[SKIPPED] += 1
                continue
            try:
                with open(ws_script, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new_text)
                rc, out, err = run_battery(ws_test, cwd=ws, timeout=timeout)
            finally:
                # guaranteed rollback of the copy before the next mutant
                with open(ws_script, "w", encoding="utf-8", newline="") as fh:
                    fh.write(clean_text)

            if rc is None:
                rows.append({"name": name, "outcome": CRASH, "cases": [],
                             "detail": err})
                counts[CRASH] += 1
            elif TRACEBACK_MARK in err:
                exc_line = next((ln for ln in err.splitlines()[::-1] if ln.strip()), "").strip()
                rows.append({"name": name, "outcome": CRASH, "cases": [],
                             "detail": "battery crashed: %s" % exc_line})
                counts[CRASH] += 1
            elif rc != 0 and not out.strip() and baseline_speaks:
                # a battery that speaks on the baseline printed NOTHING to stdout here -
                # no check was executed. The sign does not rely on the names of checks.
                err_line = next((ln for ln in err.splitlines()[::-1] if ln.strip()), "").strip()
                rows.append({"name": name, "outcome": NOSTART, "cases": [],
                             "detail": "exit=%s, battery stdout empty - no check executed "
                                       "(died before start, e.g. sys.exit on import)%s"
                                       % (rc, ("; stderr: %s" % err_line) if err_line else "")})
                counts[NOSTART] += 1
            elif rc != 0:
                cases = extract_red_cases(out)
                rows.append({"name": name, "outcome": COVERED, "cases": cases,
                             "detail": "exit=%s%s" % (rc, "" if baseline_speaks else
                                                      "; battery silent on baseline too - "
                                                      "start not verified by output")})
                counts[COVERED] += 1
            else:
                rows.append({"name": name, "outcome": HOLE, "cases": [],
                             "detail": "exit=0, same as baseline - nothing turned red"})
                counts[HOLE] += 1

        verdict_code = 0 if (counts[HOLE] == 0 and counts[CRASH] == 0 and counts[NOSTART] == 0
                             and counts[SKIPPED] == 0) else 1
        return {"baseline_rc": base_rc, "baseline_ok": True, "rows": rows,
                "baseline_speaks": baseline_speaks,
                "counts": counts, "verdict_code": verdict_code}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def print_report(result, script_path, test_path):
    print("Subject: %s\nBattery: %s" % (script_path, test_path))
    if not result["baseline_ok"]:
        print(result["baseline_note"])
        return
    print("Baseline: exit 0 (clean)\n")
    rows = result["rows"]
    if not rows:
        print("No mutants given - nothing to check.")
        return
    width = max(len(r["name"]) for r in rows)
    print("%-*s  %-28s  %s" % (width, "mutant", "outcome", "red tests / details"))
    print("%-*s  %-28s  %s" % (width, "-" * width, "-" * 28, "-" * 30))
    for r in rows:
        cases = ", ".join(r["cases"]) if r["cases"] else r["detail"]
        print("%-*s  %-28s  %s" % (width, r["name"], r["outcome"], cases))
    c = result["counts"]
    print("\nTotal: COVERED %d · HOLE %d · NOT COUNTED %d (crash %d · no start %d) · SKIPPED %d"
          % (c[COVERED], c[HOLE], c[CRASH] + c[NOSTART], c[CRASH], c[NOSTART], c[SKIPPED]))
    if c[HOLE]:
        print("HOLES in the battery: a mutation went UNNOTICED.")
    if c[CRASH]:
        print("WARNING: on some mutants the battery crashed with an unhandled exception - "
              "that is not a check; fix the exception handling in the battery.")
    if c[NOSTART]:
        print("WARNING: on some mutants the battery exited non-zero without printing A SINGLE "
              "line - zero checks executed, the catch is not proven. A check that catches "
              "this defect is needed, not the death of the battery before its start.")
    mute = (sum(1 for r in rows if r["outcome"] == COVERED)
            if not result.get("baseline_speaks", True) else 0)
    if mute:
        print("WARNING: Battery silent on baseline too: %d mutants are COVERED without a start "
              "check - death before the first check cannot be told from a catch here "
              "(limit (2) of the header)." % mute)
    if c[SKIPPED]:
        print("WARNING: some mutants have an anchor that is not unique in the copy - the "
              "mutation was not applied; recheck the anchor.")


# ------------------------------------------------------------------------ durable record

RESULT_SUFFIX = ".mutations-result.json"
SCHEMA = "mutate-check/2"   # /2 added the "no start" outcome


def default_result_path(script_path):
    """Default destination: next to the script, named after its stem.
    `.../tool.py` -> `.../tool.mutations-result.json` (symmetric to the input
    `tool.mutations.json`)."""
    stem, _ext = os.path.splitext(os.path.abspath(script_path))
    return stem + RESULT_SUFFIX


def short_path(path):
    """Path for the durable file: relative to the current folder if the subject lies inside
    it, absolute otherwise. Absolute paths with a drive letter in a commit are noise: they
    tie the evidence to one machine and differ for every reader."""
    if not path:
        return None
    ap = os.path.abspath(path)
    try:
        rel = os.path.relpath(ap, os.getcwd())
    except ValueError:                     # another drive on Windows - no relative path
        return ap
    return rel.replace(os.sep, "/") if not rel.startswith("..") else ap


def build_report(result, script_path, test_path, mut_path, timeout, only=None):
    """Payload of the durable file. Outcomes are carried over with THE SAME literals as in the
    table, and the vocabulary of all five is put next to them - collapsing to "covered/hole"
    is forbidden here. `only` - the names from `--only`: the file itself says that the result
    is PARTIAL; None - the whole corpus."""
    rows = [
        {"name": r["name"], "outcome": r["outcome"],
         "cases": list(r.get("cases") or []), "detail": r.get("detail", "")}
        for r in result.get("rows", [])
    ]
    return {
        "schema": SCHEMA,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                                  .replace(microsecond=0).isoformat(),
        "script": short_path(script_path),
        "battery": short_path(test_path),
        "mutations_file": short_path(mut_path),
        "only": list(only) if only is not None else None,
        "timeout": timeout,
        "baseline_ok": result.get("baseline_ok"),
        "baseline_rc": result.get("baseline_rc"),
        "baseline_note": result.get("baseline_note"),
        # without this flag COVERED from a silent battery would look the same in the file as
        # COVERED from a speaking one (start not verified), and the note would live only in `detail`
        "baseline_speaks": result.get("baseline_speaks"),
        "verdict_code": result.get("verdict_code"),
        "outcomes_vocabulary": [COVERED, HOLE, CRASH, NOSTART, SKIPPED],
        "counts": dict(result.get("counts") or {}),
        "rows": rows,
    }


def save_report(payload, path):
    """Write the durable file. Returns (ok: bool, message). Loses nothing silently: a write
    failure goes up and becomes exit code 2."""
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
            fh.write("\n")
    except OSError as exc:
        return False, "result NOT saved (%s): %s" % (type(exc).__name__, exc)
    return True, path


def main():
    argv = sys.argv[1:]
    # help comes BEFORE parsing and before any write: it is help and code 0, not an error
    if "-h" in argv or "--help" in argv:
        print(__doc__ or "usage: mutate_check.py <script.py> <test_script.py> <mutations.json> "
              "[--timeout N] [--save] [--out FILE]")
        return 0
    timeout = 60
    out_path = None
    save = False
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv):
            die("--out requires a file path")
        out_path = argv[i + 1]
        save = True
        del argv[i:i + 2]
    if "--save" in argv:
        argv.remove("--save")
        save = True
    only = None
    if "--only" in argv:
        # a subset of the corpus by names, without a subset file (comma-separated)
        i = argv.index("--only")
        if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
            die("--only requires a comma-separated list of names")
        only = []
        for n in argv[i + 1].split(","):
            if n.strip() and n.strip() not in only:
                only.append(n.strip())
        del argv[i:i + 2]
        if not only:
            die("--only: empty list of names")
        if save and out_path is None:
            # the durable file next to the subject attests the WHOLE corpus; a subset would overwrite it
            die("--only is incompatible with --save: the file next to the subject attests the whole "
                "corpus; a subset result goes into an explicit file via --out <path.json>")
    anchors_only = "--anchors-only" in argv
    if anchors_only:
        argv.remove("--anchors-only")
        if save:
            # the durable file attests a RUN; overwriting it without a run would erase that
            die("--anchors-only is incompatible with --save/--out: there is no run, nothing to save")
    if "--timeout" in argv:
        i = argv.index("--timeout")
        # a missing or non-integer value is code 2 "wrong call", not a traceback with code 1,
        # which by the documentation means "there is a HOLE"
        if i + 1 >= len(argv) or not (argv[i + 1].isascii() and argv[i + 1].isdigit()):
            die("--timeout requires a whole number of seconds (digits 0-9 only, no sign)")
        timeout = int(argv[i + 1])
        del argv[i:i + 2]
    if len(argv) != 3:
        die("usage: mutate_check.py <script.py> <test_script.py> <mutations.json> [--timeout N]")
    script_path, test_path, mut_path = argv
    if not os.path.isfile(mut_path):
        die("mutations file not found: %s" % mut_path)
    try:
        with open(mut_path, encoding="utf-8") as fh:
            mutations = json.load(fh)
    except (OSError, ValueError) as exc:
        die("could not read/parse the mutations JSON (%s): %s" % (type(exc).__name__, exc))
    if not isinstance(mutations, list) or not mutations:
        die("the mutations file must be a non-empty list of objects {name, anchor, replace}")
    for m in mutations:
        if not all(k in m for k in ("name", "anchor", "replace")):
            die("every mutant must have name/anchor/replace: %r" % m)
    if only is not None:
        names = {m["name"] for m in mutations}
        missing = [n for n in only if n not in names]
        if missing:
            die("--only: names not in the corpus: %s" % ", ".join(missing))
        total = len(mutations)
        mutations = [m for m in mutations if m["name"] in only]      # order as in the corpus
        print("Subset --only: %d of %d mutants in the corpus" % (len(mutations), total))

    if anchors_only:
        if not os.path.isfile(script_path):
            die("script not found: %s" % script_path)
        ar = check_anchors(script_path, mutations)
        print("Subject: %s\nAnchors (no battery run): mutants inspected %d · "
              "non-unique anchors %d · duplicate names %d"
              % (script_path, ar["total"], len(ar["bad"]), len(ar["dup_names"])))
        for name, count in ar["bad"]:
            print("  %s: occurrences %d (need 1)" % (name, count))
        for name in ar["dup_names"]:
            print("  duplicate name: %s" % name)
        return 1 if (ar["bad"] or ar["dup_names"]) else 0

    result = run_mutations(script_path, test_path, mutations, timeout=timeout)
    print_report(result, script_path, test_path)

    if save:
        target = out_path or default_result_path(script_path)
        ok, msg = save_report(
            build_report(result, script_path, test_path, mut_path, timeout, only=only), target)
        if not ok:
            print("ERROR: %s" % msg)
            return 2
        print("\nResult saved: %s" % msg)
    else:
        print("\n(result not saved anywhere - the --save flag gives a durable record)")

    return result["verdict_code"]


if __name__ == "__main__":
    sys.exit(main())
