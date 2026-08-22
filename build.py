#!/usr/bin/env python3
"""Validate questions.json and bake a render-ready PL-900 bank into index.html.

questions.json is the source of truth. It carries the raw PDF extraction for the
interactive question types (hotspot, drag and drop) as `interaction`, including
the normalized text-box geometry of the answer-area images. This script turns
that geometry back into structured widgets -- statement tables, dropdown rows,
match targets, ordered steps -- and writes the slim result into index.html as a
single `const BANK = ...;` line.

Usage:
    python3 build.py            validate, derive, write index.html
    python3 build.py --check    validate and report only, write nothing
    python3 build.py --report   per-question derivation detail for spot checks
    python3 build.py focused    restrict any of the above to one bank
"""

from __future__ import annotations

import collections
import difflib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PAGE = ROOT / "index.html"

Bank = collections.namedtuple("Bank", "name file var label")

# The banks are separate all the way down: each gets its own anchor line in
# index.html, and nothing here or in the app ever concatenates them.
BANKS = [
    Bank("full", "questions.json", "BANK", "Full"),
    Bank("focused", "focused-questions.json", "BANK_FOCUS", "Focused"),
]

SRC = ROOT / BANKS[0].file          # kept for the sync check in CLAUDE.md


def anchor(var):
    return re.compile(r"^const %s = .*;$" % var, re.M)

# The answer-area images are OCRed, and the OCR confuses a handful of glyphs the
# same way every time. Fix them for display; norm() fixes them again for matching.
OCR_FIXES = [
    (r"\bAl\b", "AI"),
    (r"\bPower Bl\b", "Power BI"),
    (r"\bPower BI\b", "Power BI"),
    (r"\bBl\b", "BI"),
    (r"\bUl\b", "UI"),
    (r"\bPowerApps\b", "Power Apps"),
    (r"\btempiate\b", "template"),
    (r"\bCusto\b", "Customer"),
    (r"\bAPls\b", "APIs"),
    (r"\bPower Bi\b", "Power BI"),
    # A digit and its enclosing keycap arrived separated; render it as a list number.
    (r"(\d)\s*\u20e3", r"\1."),
]
STRAY = re.compile(r"(?:^|\s)[O0](?=\s|$)")          # unfilled radio buttons OCR as O
END = re.compile(r"[.?!:]\s*$")
HEADER_TOL = 0.045
COL_TOL = 0.06


GLYPHS = {"\u2030": " ", "\u05c0": "T", "\ufb02": "fl", "\ufb01": "fi",
          "\u200b": "", "\u202f": " ", "\ufffd": "", "\u2711": "\u2022"}


def prose(text: str) -> str:
    """Stem and explanation text: fix the OCR's stock confusions, keep the lines."""
    for bad, good in GLYPHS.items():
        text = text.replace(bad, good)
    out = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        for pat, rep in OCR_FIXES:
            line = re.sub(pat, rep, line)
        out.append(line)
    return "\n".join(out).strip()


def clean(text: str) -> str:
    text = str(text)
    for bad, good in GLYPHS.items():
        text = text.replace(bad, good)
    text = re.sub(r"\s+", " ", text).strip()
    text = STRAY.sub(" ", text)
    for pat, rep in OCR_FIXES:
        text = re.sub(pat, rep, text)
    text = re.sub(r"^[\[\](){}|<>*.,;:\-—•]+\s*", "", text)
    text = re.sub(r"\s*[\[\](){}|<>]+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def norm(text: str) -> str:
    text = clean(text).lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def words(text: str) -> int:
    return len(clean(text).split())


# ---------------------------------------------------------------- geometry ----
def strip_headers(els, protect=()):
    """Drop the table's column headers.

    Headers sit in a band at the top: every element short, at least two of them
    across the columns (or 'Answer Area' on its own), and none of them a text
    that the answer key uses -- a short answer repeated in the pool and the
    answer column looks exactly like a header row otherwise. Walk bands down
    from the top and stop at the first one that fails.
    """
    els = sorted(els, key=lambda e: -e["y"])
    headers, drop = [], set()
    i = 0
    while i < len(els):
        band, j = [els[i]], i + 1
        while j < len(els) and abs(els[j]["y"] - els[i]["y"]) <= HEADER_TOL:
            band.append(els[j])
            j += 1
        short = all(words(e["text"]) <= 3 for e in band)
        lone_area = len(band) == 1 and norm(band[0]["text"]) == "answer area"
        clash = any(sim(e["text"], p) >= 0.8 for e in band for p in protect)
        if short and (len(band) >= 2 or lone_area) and not clash:
            for e in band:
                drop.add(id(e))
                if norm(e["text"]) != "answer area":
                    headers.append({"text": clean(e["text"]), "x": e["x"]})
            i = j
        else:
            break
    kept = [e for e in els if id(e) not in drop and norm(e["text"]) != "answer area"]
    return kept, headers


def label_for(headers, x, taken=()):
    """The header sitting closest to a column's left edge."""
    pool = [h for h in headers if h["text"] not in taken]
    if not pool:
        return None
    best = min(pool, key=lambda h: abs(h["x"] - x))
    return best["text"] if abs(best["x"] - x) < 0.25 else None


def columns(els, tol=COL_TOL):
    """Cluster elements into left-aligned columns, left to right."""
    cols = []
    for e in sorted(els, key=lambda e: e["x"]):
        for c in cols:
            if abs(c["x"] - e["x"]) <= tol:
                c["items"].append(e)
                c["x"] = sum(i["x"] for i in c["items"]) / len(c["items"])
                break
        else:
            cols.append({"x": e["x"], "items": [e]})
    for c in cols:
        c["items"].sort(key=lambda e: -e["y"])
    return sorted(cols, key=lambda c: c["x"])


def visual(q):
    inter = q.get("interaction") or {}
    for key in ("answer_visuals", "source_visuals"):
        vis = inter.get(key) or []
        if vis and vis[0].get("elements"):
            return vis[0]
    return None


def join_lines(lines):
    """Join PDF line wrapping into whole statements, breaking on end punctuation."""
    out, cur = [], []
    for line in lines:
        cur.append(line)
        if END.search(line):
            out.append(clean(" ".join(cur)))
            cur = []
    if cur:
        out.append(clean(" ".join(cur)))
    return out


def sentences(lines):
    text = clean(" ".join(lines))
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"“])', text)
    return [p.strip() for p in parts if p.strip()]


def group_to(lines, n):
    """Best grouping of wrapped lines into n statements, or None."""
    lines = [clean(l) for l in lines if clean(l)]
    for candidate in (lines, join_lines(lines), sentences(lines)):
        if len(candidate) == n:
            return candidate
    return None


def match_one(target, pool, cutoff=0.7):
    """Best fuzzy match for target among pool items (list of strings)."""
    best, score = None, 0.0
    for item in pool:
        s = sim(target, item)
        if s > score:
            best, score = item, s
    return best if score >= cutoff else None


# ---------------------------------------------------------------- builders ----
def build_choice(q):
    labels = [c["label"] for c in q["choices"]]
    correct = [c for c in q["correct"] if c in labels]
    if len(correct) != len(q["correct"]):
        return None
    return {
        "kind": "choice",
        "choices": [{"label": c["label"], "text": clean(c["text"])} for c in q["choices"]],
        "correct": correct,
    }


def yesno_lines(q):
    """Statement lines from the answer-area image, top to bottom.

    Prefer the geometry over the flat `statements` list because it shows where
    the 'Statement' column header is: anything above it is the image's caption,
    not a statement, and the flat list has no way to tell them apart.
    """
    vis = visual(q)
    if not vis:
        return None
    els = [e for e in vis["elements"] if norm(e["text"]) != "answer area"]
    header = [e for e in els if norm(e["text"]) in ("statement", "statements")]
    if header:
        cut = max(e["y"] for e in header)
        els = [e for e in els if e["y"] < cut - 0.01]
    else:
        els, _ = strip_headers(els)
    keep = []
    for e in sorted(els, key=lambda e: -e["y"]):
        text = clean(e["text"])
        if not text or norm(text) in ("yes", "no", "d", "e", "statement", "statements"):
            continue
        if e["x"] > 0.62 and words(text) <= 2:          # the Yes/No radio column
            continue
        keep.append(text)
    return keep or None


def build_yesno(q):
    answers = [clean(a).capitalize() for a in q["correct"]]
    if not answers or any(a not in ("Yes", "No") for a in answers):
        return None
    flat = [clean(l) for l in ((q.get("interaction") or {}).get("statements") or []) if clean(l)]
    geo = yesno_lines(q)
    stmts, note = None, None
    for source in (geo, flat):
        if source:
            stmts = group_to(source, len(answers))
            if stmts:
                break
    if stmts is None:
        lines = geo or flat
        if not lines:
            return None
        stmts = join_lines(lines)
        note = ("The statements and the recorded answers do not line up "
                f"({len(stmts)} statements, {len(answers)} answers), so any extra rows are not graded.")
    rows = [{"text": s, "answer": answers[i] if i < len(answers) else None}
            for i, s in enumerate(stmts)]
    if len(answers) > len(stmts):
        note = f"Only {len(stmts)} of the {len(answers)} statements survived extraction."
        rows = rows[: len(stmts)]
    if not any(r["answer"] for r in rows):
        return None
    return {"kind": "yesno", "rows": rows, "note": note}


def split_runs(items, n):
    """Split y-ordered dropdown options into n runs at the largest vertical gaps."""
    if n <= 1:
        return [items]
    gaps = sorted(range(1, len(items)), key=lambda i: items[i - 1]["y"] - items[i]["y"], reverse=True)
    cuts = sorted(gaps[: n - 1])
    runs, prev = [], 0
    for c in cuts:
        runs.append(items[prev:c])
        prev = c
    runs.append(items[prev:])
    return [r for r in runs if r]


def build_dropdown(q):
    vis = visual(q)
    if not vis:
        return None
    kept, headers = strip_headers(vis["elements"], q["correct"])
    if not kept:
        return None
    n = len(q["correct"])
    cols = columns(kept)
    opt_col = max(cols, key=lambda c: len(c["items"]))
    if len(opt_col["items"]) < max(2, n):
        return None
    runs = split_runs(opt_col["items"], n)
    if len(runs) != n:
        return None
    opt_ids = {id(e) for e in opt_col["items"]}
    prompts = [e for e in kept if id(e) not in opt_ids]
    tops = [max(e["y"] for e in run) for run in runs]
    buckets = collections.defaultdict(list)
    for e in sorted(prompts, key=lambda e: -e["y"]):
        # a prompt belongs to the run whose option list opens just below it
        above = [i for i, t in enumerate(tops) if t <= e["y"] + 0.05]
        buckets[above[0] if above else len(tops) - 1].append(clean(e["text"]))
    rows = []
    for i, run in enumerate(runs):
        options = [clean(e["text"]) for e in run]
        seen, uniq = set(), []
        for o in options:
            if norm(o) not in seen and o:
                seen.add(norm(o))
                uniq.append(o)
        answer = match_one(q["correct"][i], uniq)
        if answer is None:
            answer = clean(q["correct"][i])
            uniq.append(answer)
        rows.append({"prompt": " ".join(buckets.get(i, [])).strip(), "options": uniq, "answer": answer})
    if all(not r["prompt"] for r in rows):
        return None
    px = min((e["x"] for e in prompts), default=0.0)
    left = label_for(headers, px)
    right = label_for(headers, opt_col["x"], (left,) if left else ())
    return {"kind": "dropdown", "headers": [left, right], "rows": rows}


def build_match(q):
    vis = visual(q)
    if not vis:
        return None
    kept, headers = strip_headers(vis["elements"], q["correct"])
    n = len(q["correct"])
    answers, used = [], set()
    for target in q["correct"]:
        best, score = None, 0.0
        for e in kept:
            if id(e) in used or e["x"] < 0.5:
                continue
            s = sim(target, e["text"])
            if s > score:
                best, score = e, s
        if best is None or score < 0.7:
            return None
        used.add(id(best))
        answers.append(best)
    rest = [e for e in kept if id(e) not in used]
    left = [e for e in rest if e["x"] < min(a["x"] for a in answers) - 0.05]
    if not left:
        return None
    cols = columns(left)
    prompt_col = max(cols, key=lambda c: max(e["w"] for e in c["items"]))
    prompt_ids = {id(e) for e in prompt_col["items"]}
    # A header that shares a band with a real answer survives strip_headers. Pick
    # those up here: short, above all content, and not in the drag pool's columns.
    ceiling = max([e["y"] for e in list(answers) + prompt_col["items"]])
    inline = [e for e in rest if id(e) not in prompt_ids and e["y"] > ceiling
              and words(e["text"]) <= 3 and e["x"] >= prompt_col["x"] - 0.05]
    headers = headers + [{"text": clean(e["text"]), "x": e["x"]} for e in inline]
    skip = {id(e) for e in inline}
    # the pool sits to the left of the prompts, never between them and the answers
    pool = [clean(e["text"]) for c in cols if c is not prompt_col and c["x"] < prompt_col["x"]
            for e in c["items"] if id(e) not in skip]
    buckets = collections.defaultdict(list)
    for e in sorted(prompt_col["items"], key=lambda e: -e["y"]):
        k = min(range(n), key=lambda j: abs(answers[j]["y"] - e["y"]))
        buckets[k].append(clean(e["text"]))
    # An answer box aligned with the top of its row rather than centered on it
    # pulls the row's last wrapped line into the row below. The tell is a prompt
    # left hanging mid-sentence above a row that opens on a lowercase word.
    for k in range(n - 1):
        while (buckets.get(k) and len(buckets.get(k + 1, [])) > 1
               and not END.search(buckets[k][-1]) and buckets[k + 1][0][:1].islower()):
            buckets[k].append(buckets[k + 1].pop(0))
    if any(not buckets.get(k) for k in range(n)):
        return None
    rows = [{"prompt": " ".join(buckets[k]).strip(), "answer": clean(answers[k]["text"])} for k in range(n)]
    if any(sim(r["prompt"], r["answer"]) >= 0.85 for r in rows):
        return None
    seen, uniq = set(), []
    for item in pool + [r["answer"] for r in rows]:
        if item and norm(item) not in seen:
            seen.add(norm(item))
            uniq.append(item)
    for r in rows:
        hit = match_one(r["answer"], uniq, 0.85)
        if hit:
            r["answer"] = hit
    if len(uniq) < 2:
        return None
    left = label_for(headers, prompt_col["x"])
    taken = (left,) if left else ()
    right = label_for(headers, sum(a["x"] for a in answers) / len(answers), taken)
    taken = tuple(t for t in (left, right) if t)
    pool_label = label_for(headers, min((c["x"] for c in cols if c is not prompt_col), default=0.0), taken)
    return {"kind": "match", "headers": [left, right], "pool": uniq, "rows": rows,
            "poolLabel": pool_label}


def build_match2(q):
    """Match layout where the answer column is empty or unreadable in the image.

    Falls back to pure geometry: the widest left column carries the prompts, the
    narrow columns to its left are the drag pool, and the answer key supplies
    what goes in each target.
    """
    vis = visual(q)
    if not vis:
        return None
    kept, headers = strip_headers(vis["elements"], q["correct"])
    n = len(q["correct"])
    if len(kept) < n:
        return None
    cols = columns(kept)
    body = [c for c in cols if c["x"] < 0.6] or cols
    prompt_col = max(body, key=lambda c: max(e["w"] for e in c["items"]))
    if len(prompt_col["items"]) < n:
        return None
    runs = split_runs(prompt_col["items"], n)
    if len(runs) != n:
        return None
    pool = [clean(e["text"]) for c in cols if c is not prompt_col and c["x"] < prompt_col["x"]
            for e in c["items"]]
    seen, uniq = set(), []
    for item in pool:
        if item and norm(item) not in seen:
            seen.add(norm(item))
            uniq.append(item)
    rows = []
    for i, run in enumerate(runs):
        prompt = " ".join(clean(e["text"]) for e in run).strip()
        answer = match_one(q["correct"][i], uniq, 0.75)
        if answer is None:
            answer = clean(q["correct"][i])
            if norm(answer) not in seen:
                seen.add(norm(answer))
                uniq.append(answer)
        rows.append({"prompt": prompt, "answer": answer})
    if any(not r["prompt"] for r in rows) or len(uniq) < 2:
        return None
    if any(sim(r["prompt"], r["answer"]) >= 0.85 for r in rows):
        return None
    left = label_for(headers, prompt_col["x"])
    taken = (left,) if left else ()
    right = label_for(headers, 0.8, taken)
    taken = tuple(t for t in (left, right) if t)
    pool_label = label_for(headers, min((c["x"] for c in cols if c is not prompt_col), default=0.0), taken)
    return {"kind": "match", "headers": [left, right], "pool": uniq, "rows": rows,
            "poolLabel": pool_label}


def build_order(q):
    inter = q.get("interaction") or {}
    pool = [clean(t) for t in (inter.get("source_items") or []) if clean(t)]
    vis = visual(q)
    if vis:
        kept, _ = strip_headers(vis["elements"])
        pool += [clean(e["text"]) for e in kept]
    seen, uniq = set(), []
    for item in pool:
        if norm(item) and norm(item) not in seen and words(item) > 1:
            seen.add(norm(item))
            uniq.append(item)
    answer = []
    for step in q["correct"]:
        hit = match_one(step, uniq, 0.7)
        if hit is None:
            hit = clean(step)
            if norm(hit) not in seen:
                seen.add(norm(hit))
                uniq.append(hit)
        answer.append(hit)
    if len(answer) != len(set(norm(a) for a in answer)):
        return None
    if len(uniq) < len(answer):
        return None
    return {"kind": "order", "pool": uniq, "answer": answer}


def build_pick(q):
    """Fallback: choose the correct items out of the extracted pool."""
    inter = q.get("interaction") or {}
    pool = [clean(t) for t in (inter.get("source_items") or []) if clean(t)]
    seen, uniq = set(), []
    for item in pool:
        if item and norm(item) not in seen:
            seen.add(norm(item))
            uniq.append(item)
    answers = []
    for target in q["correct"]:
        hit = match_one(target, uniq, 0.7)
        if hit is None:
            hit = clean(target)
            if norm(hit) not in seen:
                seen.add(norm(hit))
                uniq.append(hit)
        answers.append(hit)
    if len(uniq) < len(answers) + 1:
        return None
    labels = {}
    choices = []
    for i, text in enumerate(uniq):
        label = "abcdefghijklmnopqrstuvwxyz"[i].upper() if i < 26 else str(i)
        labels[norm(text)] = label
        choices.append({"label": label, "text": text})
    correct = sorted({labels[norm(a)] for a in answers})
    if len(correct) != len(set(norm(a) for a in answers)):
        return None
    return {"kind": "choice", "choices": choices, "correct": correct}


BUILDERS = {
    "multiple_choice": [build_choice],
    "multiple_select": [build_choice],
    "hotspot_yes_no": [build_yesno],
    "hotspot": [build_dropdown, build_pick, build_choice],
    "drag_and_drop": [build_match, build_match2, build_pick, build_choice],
    "drag_and_drop_ordering": [build_order, build_pick, build_choice],
}

KIND_LABEL = {
    "choice": "Multiple choice",
    "yesno": "Yes / No",
    "dropdown": "Select from lists",
    "match": "Drag and drop",
    "order": "Put in order",
}


NATIVE_KIND = {
    "multiple_choice": "choice",
    "multiple_select": "choice",
    "hotspot_yes_no": "yesno",
    "hotspot": "dropdown",
    "drag_and_drop": "match",
    "drag_and_drop_ordering": "order",
}


def derive(q):
    """Return (ui, degraded) — the render-ready widget, and whether the widget
    it deserves could not be rebuilt and it is showing as a plain list."""
    for builder in BUILDERS.get(q["question_type"], [build_choice]):
        ui = builder(q)
        if ui:
            return ui, ui["kind"] != NATIVE_KIND.get(q["question_type"])
    return None, True


def answer_count(ui):
    if ui["kind"] == "choice":
        return len(ui["correct"])
    if ui["kind"] == "yesno":
        return sum(1 for r in ui["rows"] if r["answer"])
    if ui["kind"] in ("dropdown", "match"):
        return len(ui["rows"])
    return len(ui["answer"])


def normalize(qs):
    """Derived and optional fields, fixed up in place before validation."""
    for q in qs:
        for field in ("explanation", "review"):
            if field in q and (not isinstance(q[field], str) or not q[field].strip()):
                q.pop(field)
            elif field in q:
                q[field] = q[field].strip()
        q["multi"] = len(q.get("correct", [])) > 1
    return qs


def validate(qs):
    problems = []
    for i, q in enumerate(qs, 1):
        where = f"q{i}"
        if not clean(q.get("q", "")):
            problems.append(f"{where}: empty question text")
        if not q.get("correct"):
            problems.append(f"{where}: no correct answer")
        if q.get("question_type") not in BUILDERS:
            problems.append(f"{where}: unknown question_type {q.get('question_type')!r}")
        if not q.get("topic"):
            problems.append(f"{where}: no topic")
        for field in ("explanation", "review"):
            val = q.get(field)
            if val is not None and (not isinstance(val, str) or not val.strip()):
                problems.append(f"{where}: {field} present but blank")
        if q.get("question_type") in ("multiple_choice", "multiple_select"):
            labels = [c["label"] for c in q.get("choices", [])]
            if len(labels) < 2:
                problems.append(f"{where}: fewer than 2 choices")
            if len(set(labels)) != len(labels):
                problems.append(f"{where}: duplicate choice labels")
            for c in q["correct"]:
                if c not in labels:
                    problems.append(f"{where}: correct label {c!r} is not a choice")
    return problems


def dedupe(qs):
    seen, out = {}, []
    for q in qs:
        inter = q.get("interaction") or {}
        body = " ".join((inter.get("statements") or []) + (inter.get("source_items") or []))
        key = (q["question_type"],
               re.sub(r"\s+", " ", q["q"].strip().lower()).rstrip(".?:"),
               norm(body), tuple(norm(c) for c in q["correct"]))
        if key in seen:
            print(f"  dropped duplicate: {q['q'][:70]}...")
            continue
        seen[key] = True
        out.append(q)
    return out


def bake(qs):
    """Turn the raw bank into the slim records the page renders."""
    out, degraded, dropped = [], 0, []
    for n, q in enumerate(qs, 1):
        ui, fell_back = derive(q)
        if ui is None:
            dropped.append((n, q["question_type"], q["q"][:60]))
            continue
        rec = {
            "q": prose(q["q"]),
            "topic": q["topic"],
            "type": q["question_type"],
            "kind": ui["kind"],
            "n": answer_count(ui),
        }
        rec.update({k: v for k, v in ui.items() if k not in ("kind", "note") and v is not None})
        if q.get("explanation"):
            rec["explanation"] = prose(q["explanation"])
        notes = []
        if fell_back:
            notes.append("The answer area for this question could not be reconstructed from the PDF, "
                         "so it is shown as a plain selection. Wording may be rough.")
        if ui.get("note"):
            notes.append(ui["note"])
        if q.get("review"):
            notes.append(q["review"].strip())
        if notes:
            rec["review"] = " ".join(notes)
            degraded += 1
        out.append(rec)
    return out, degraded, dropped


def report(bank):
    kinds = collections.Counter(r["kind"] for r in bank)
    topics = collections.Counter(r["topic"] for r in bank)
    flagged = sum(1 for r in bank if r.get("review"))
    withexp = sum(1 for r in bank if r.get("explanation"))
    multi = sum(1 for r in bank if r["n"] > 1)
    print(f"{len(bank)} questions · {multi} multi-answer · {withexp} with explanations · {flagged} flagged for review")
    for k, c in kinds.most_common():
        print(f"  {KIND_LABEL[k]:<20} {c}")
    print()
    for t, c in topics.most_common():
        print(f"  {t:<26} {c}")


def main(argv):
    check = "--check" in argv
    detail = "--report" in argv
    only = [a for a in argv if not a.startswith("--")]
    banks = [b for b in BANKS if not only or b.name in only]
    if not banks:
        print("no such bank; known: " + ", ".join(b.name for b in BANKS), file=sys.stderr)
        return 1

    built = []
    for b in banks:
        path = ROOT / b.file
        if not path.exists():
            print(f"{b.file}: missing", file=sys.stderr)
            return 1
        print(f"\n=== {b.label} ({b.file}) ===")
        qs = normalize(json.loads(path.read_text(encoding="utf-8")))
        before = len(qs)
        problems = validate(qs)
        if problems:
            print(f"{len(problems)} problem(s) in {b.file}:")
            for p in problems:
                print("  " + p)
            return 1
        qs = dedupe(qs)
        records, degraded, dropped = bake(qs)
        if dropped:
            print(f"{len(dropped)} question(s) could not be rendered at all and were dropped:")
            for n, t, text in dropped:
                print(f"  #{n} [{t}] {text}...")
        print(f"read {before} · kept {len(records)}")
        report(records)
        if detail:
            for i, r in enumerate(records, 1):
                print(f"\n#{i} [{r['kind']}] {r['q'][:80]}")
                print("   " + json.dumps({k: v for k, v in r.items() if k not in ("q", "explanation")}, ensure_ascii=False)[:400])
        built.append((b, qs, records))

    if check or detail:
        return 0

    page = PAGE.read_text(encoding="utf-8")
    for b, _, records in built:
        pat = anchor(b.var)
        if len(pat.findall(page)) != 1:
            print(f"index.html: expected exactly one 'const {b.var} = ...;' line", file=sys.stderr)
            return 1
        line = f"const {b.var} = " + json.dumps(records, ensure_ascii=False) + ";"
        page = pat.sub(lambda m: line, page, count=1)
    PAGE.write_text(page, encoding="utf-8")
    for b, qs, _ in built:
        (ROOT / b.file).write_text(json.dumps(qs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {PAGE.name} ({len(page) // 1024} KB, {len(built)} bank(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
