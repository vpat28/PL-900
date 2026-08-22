# PL-900 practice app

A static, single-page practice and mock-exam tool for **PL-900 · Microsoft Power
Platform Fundamentals**, with the question banks baked into the page. No server,
no dependencies, no network calls. It works by double-clicking `index.html`.

**Two banks, never mixed.** A tab strip on the setup screen picks between
**Full** (381 questions, everything extracted from the PDF, in the wording the
dump used) and **Focused** (230, a curated subset of the same questions
rewritten in current Microsoft terminology). A session draws from exactly one of
them; nothing in the app or the build ever concatenates them. Both offer all
three modes, and the active bank is named in the sticky bar during a session and
in the results header. See *Known data issues* before treating them as
independent material.

**Unofficial.** Not affiliated with, endorsed by, or reviewed by Microsoft. The
bank is community-quality material extracted from a PDF dump — see *Known data
issues* before trusting any single answer.

---

## Repo layout

| File | Role |
| --- | --- |
| `questions.json` | **Source of truth for the Full bank** (382 raw → 381 rendered). Humans edit this. |
| `focused-questions.json` | **Source of truth for the Focused bank** (230, modernized wording). Same schema, same pipeline. |
| `index.html` | The whole app: markup, CSS, JS, and a generated copy of the bank. |
| `build.py` | Validates `questions.json`, rebuilds the answer-area widgets, injects the bank into `index.html`. |
| `build_questions.py` | The upstream extractor: `pl-900.pdf` → `questions.json`. Not part of the app build. |
| `test.svg` | Source glyph for the favicon — a certified-document mark. The page inlines it; the file is only kept so the shape can be re-colored or re-inset later. |
| `pl-900.pdf` | The source dump. **Not committed** (32 MB, third-party material) — keep a local copy if you want to re-run `build_questions.py`. |

### The one rule

`index.html` contains a generated copy of each bank on a single line — `const
BANK = ` for Full, `const BANK_FOCUS = ` for Focused. **Never hand-edit those
lines.** Edit the `.json`, then run `python3 build.py`.

All three files are committed — `index.html` has to carry the data so the page
stays self-contained. Any change to a bank is a two-file commit; a diff that
touches a `.json` and not `index.html` means the build step was skipped.

Adding a third bank means one entry in `BANKS` in `build.py` (name, file,
`const` variable, label), one `const <VAR> = [];` line in `index.html`, one
entry in the `BANKS` array in the page JS, and one `<button class="tab">` in the
strip. Nothing else in the app is bank-aware.

The baked bank is **not** a copy of `questions.json`. It is the derived,
render-ready form: the raw PDF extraction (`interaction`) is dropped and
replaced by the reconstructed widget (see *Answer areas* below). To check the
two are in sync, re-derive rather than diff:

```bash
python3 - <<'PY'
import json, pathlib, build
page = pathlib.Path("index.html").read_text()
for b in build.BANKS:
    src = build.normalize(json.loads((build.ROOT / b.file).read_text()))
    baked = json.loads(build.anchor(b.var).search(page).group(0).split(" = ", 1)[1].rstrip(";"))
    print(b.label, "in sync" if build.bake(src)[0] == baked else "DRIFTED")
PY
```

---

## Question schema (`questions.json`)

```json
{
  "q": "You create a user-owned custom entity by using Common Data Service. …",
  "choices": [{ "label": "A", "text": "Yes" }, { "label": "B", "text": "No" }],
  "correct": ["No", "Yes"],
  "multi": true,
  "question_type": "hotspot_yes_no",
  "topic": "Microsoft Dataverse",
  "explanation": "Box 1: No — …",
  "interaction": { "source_items": [], "statements": [], "source_visuals": [],
                   "answer_visuals": [], "context_visuals": [] }
}
```

- `question_type` — one of `multiple_choice`, `multiple_select`,
  `hotspot_yes_no`, `hotspot`, `drag_and_drop`, `drag_and_drop_ordering`.
- `correct` — for the plain types, choice **labels**. For every interactive
  type, the **answer texts in row order** (top to bottom, or step 1..n).
- `multi` — derived, never set by hand; the build recomputes it.
- `topic` — one of the nine buckets below. Set on every question already.
- `explanation` — optional; shown in practice mode and in the answer review.
  Omit the field rather than setting `""`; the build strips blanks.
- `review` — optional; renders as a "Needs review" caution block. The build
  adds its own notes here for questions whose extraction is imperfect.
- `interaction` — the raw extraction for interactive types. `source_items` and
  `statements` are flat OCR text; `*_visuals` carry the answer-area images'
  text boxes as `{text, x, y, w, h}` in normalized image coordinates, with
  **y measured from the bottom**. This is what makes rebuilding the widgets
  possible; do not strip it.

Topics: Power Apps · Power Automate · Power BI · Microsoft Dataverse · Power
Platform · Microsoft Copilot Studio · Power Pages · Dynamics 365 · AI Builder.
They come from the extractor, not from the app build; pin one by editing the
field.

---

## `build.py`

Python 3 standard library only. No `package.json`, no bundler, ever.

```bash
python3 build.py            # validate, derive, write index.html and both .json files
python3 build.py --check    # validate and report only, write nothing
python3 build.py --report   # per-question derivation detail for spot checks
python3 build.py focused    # restrict any of the above to one bank (full | focused)
```

Every bank runs the same pipeline and gets its own report. `index.html` is read
once, has every bank substituted into it, and is written once — a failure on the
second bank leaves the file untouched rather than half-updated.

Pipeline: `read → normalize → validate → dedupe → bake → report → write`.
`validate` collects every problem, prints them, exits 1, and writes nothing.
`--check` never opens `index.html`, so it **cannot** tell you the two have
drifted; a plain `python3 build.py` is what reconciles them.

`dedupe` keys on question type + stem + statements/source items + answers. Do
not key on the stem alone: most Yes/No questions share the boilerplate stem
"For each of the following statements, select Yes if the statement is true",
and stem-only keying silently deletes twenty good questions.

### Answer areas — the interesting part

The PDF exposes interactive questions as pictures. `build_questions.py` OCRed
those pictures and kept the text boxes with their positions; `build.py` turns
that geometry back into a widget:

| `question_type` | Widget `kind` | Rebuilt from |
| --- | --- | --- |
| `multiple_choice`, `multiple_select` | `choice` | `choices` / `correct` directly |
| `hotspot_yes_no` | `yesno` | statement lines above the "Statement" column header, grouped into whole sentences |
| `hotspot` | `dropdown` | the option column (the widest x-cluster), split into runs at the largest vertical gaps — one run per dropdown |
| `drag_and_drop` | `match` | answer texts anchored in the right column, prompts assigned to the nearest anchor, pool taken from the columns left of the prompts |
| `drag_and_drop_ordering` | `order` | `correct` is already the sequence; the pool is the action list |

Two ideas hold the reconstruction together:

- **Header bands.** Column headers sit in a band at the top: all elements
  short, at least two across the columns, none of them a text the answer key
  uses. `strip_headers` walks bands down from the top and stops at the first
  one that fails. The answer-key guard matters: a short answer that appears in
  both the pool and the answer column looks exactly like a header row.
- **Count validation.** Every builder checks that the number of rows it found
  equals `len(correct)`. When it does not, it returns `None` and the next
  builder in the chain tries. The last resort is `build_pick`, which shows the
  extracted pool as a plain multi-select and flags the question for review.

`derive` returns `(ui, degraded)`. `degraded` is true only when the widget the
question *deserves* could not be rebuilt — a `match` produced by the geometry-
only fallback is still a `match` and is not flagged.

Baked record: `q`, `topic`, `type`, `kind`, `n` (number of answers), plus the
kind's payload (`choices`/`correct`, or `rows`, `pool`, `poolLabel`, `headers`,
`answer`), plus `explanation` and `review`.

---

## The app (`index.html`)

Three `<section>`s toggled with the `hidden` attribute: `#setup`, `#quiz`,
`#results`. No framework, no router, no build step for the JS.

```js
const MOCK_N = 50, MOCK_SEC = 45 * 60;
const BANKS = [{ label, qs, note }, ...];        // Full, Focused — never concatenated
let cfg = { bank, mode, count, shufQ, shufC, onlyMulti, onlyInteractive };
let S = { qs, i, picks, done, t0, lastI, limit, tick, bank };
let held = null;   // pool item picked up in a drag-and-drop question
```

`bank()` is the active entry; `refreshBank()` repoints the whole setup screen at
it — note, stats, kind breakdown, topic count, and the length chips, which are
regenerated because the bank sizes differ. `S.bank` is captured at `start()`, so
the sticky bar and the results header name the bank the session actually drew
from even if the tab is switched afterwards.

`isTest()` means "not practice" — it is the switch for every silent-mode branch.

- `prep(src)` rebuilds a fresh object per kind and shuffles: choice labels are
  reassigned A, B, C… with `correct` remapped, dropdown options and drag pools
  are shuffled in place. **Any key not listed in `prep` is silently dropped**,
  so adding a field to the schema means adding it there too.
- `picks[i]` is an array of labels for `choice`, and one slot per row (`null`
  when empty) for every other kind.
- `slots(q)` is the answer per row; `ok(q, picks)` is set equality for `choice`
  and per-row equality otherwise. **Grading is all-or-nothing per question** —
  the real exam gives partial credit, this does not. The results screen says so.
- `widget(q, picks, done, fresh)` builds the markup for a kind and is shared by
  the quiz screen and the answer review; `done` switches on the diff gutter and
  disables every control.
- Text comparison goes through `nrm`/`same` (case- and punctuation-insensitive)
  because the OCR'd option text and the answer key differ in trailing periods.

Interaction: Yes/No radios, native `<select>`s for dropdowns, and click-then-
click *or* HTML5 drag for the pools. Click-to-place is the primary path —
dragging is a convenience, not a requirement, so the widgets work on a phone.
`1`–`9` / `a`–`j` select a choice on `choice` questions only; `Enter` fires the
primary button.

### Design conventions

The page imitates the **Microsoft exam delivery UI** (Fluent 1, as used by the
Pearson VUE delivery client): Segoe UI, `#0078D4` blue, 2px corner radius,
neutral greys `#F3F2F1`/`#E1DFDD`/`#605E5C`, flat surfaces, and a 940px content
sheet on a grey page.

The session chrome is three fixed pieces, all hidden outside a session:

| Element | Role |
| --- | --- |
| `.examtop` | Near-black title strip: exam name left, "Unofficial practice" tag right |
| `.bar` | Info strip: "Question N of M", mode, bank, running score, timer chip |
| `.actionbar` | Fixed bottom bar: keyboard hint left, Previous / Next right |

Grading feedback has no equivalent in the real UI, so it borrows Fluent's
message-bar and validation language instead: a picked-and-correct row is
`.correct` (green tint, 3px inset left rule, `✓` in the rail), picked-and-wrong
is `.wrong` (`✕`), and a missed answer is `.missed` (dashed `✓`). Every
interactive row carries the same rail, so a half-right answer area shows exactly
which rows failed. The verdict and the "needs review" note render as Fluent
message bars — tinted background, 4px left accent, no radius.

Unanswered choice rows draw a radio (`.pip`) or a checkbox (`.pip.box`) rather
than a letter, matching the exam; the letters still work as keyboard shortcuts
and the verdict names the correct **options**, not their letters.

The favicon is an inline data-URI SVG — the `test.svg` glyph in white, inset
inside a black rounded tile. It must stay inline: the page has to keep
working as a single file. The tile matters at 16px, where a bare glyph turns to
mush and where the sibling GH-900 / GH-300 apps already use a white `+` on a
colored square, so shape is what tells the tabs apart.

**This stylesheet is light-only and deliberately has no dark counterpart** — the
real exam UI has one palette, and `<meta name="color-scheme" content="light">`
keeps the form controls from being themed out from under it. That is the one
place this repo departs from the GH-900 reference app's rules. Never hard-code a
color in markup or JS; add a token to `:root`. Motion is gated on `REDUCED` in
JS and a media query in CSS.

`.want` is the green "here is the answer" annotation; `.dot.key` is the dashed
ring on the Yes/No radio you should have picked; `.mark` is the graded ✓/✕ in
the row rail. They are different things — do not merge the class names.

### Touch and small screens

The app is meant to be usable on a phone and an iPad, which the exam client
never has to be, so a few rules exist only for that:

- **Drag is never the only way.** HTML5 drag-and-drop does not fire on iOS
  Safari at all. Tap-an-item-then-tap-a-slot is the primary interaction and
  drag is the convenience on top; the hint text says "tap" or "click" depending
  on `TOUCH` (`matchMedia('(hover:none)')`). Picking an item on a touch device
  scrolls the first open slot into view, because the pool and the answer area
  do not fit on a phone together.
- **44px touch targets.** The Yes/No control is a 44px `button.dot` wrapping an
  18px `span.ring`; the button is the hit area and the ring is what you see.
  Never collapse them back into one element.
- **16px form text on touch.** `.sel` goes to 16px under `(hover:none)` — below
  that, iOS Safari zooms the page when a `<select>` takes focus, and it does not
  zoom back out.
- **Three breakpoints, not one.** 760px shrinks the chrome and stacks the mode
  cards; 620px is where the answer-area tables collapse to one column, chosen
  so an iPad in portrait (744–834px) keeps the real three-column table; and
  `(hover:none)` handles target sizes independently of width.
- The fixed action bar adds `env(safe-area-inset-bottom)`, and the sheet's
  min-height is `100dvh` with a `100vh` fallback, so the iOS toolbars do not
  cover the buttons.

Verified with device emulation over CDP at iPhone SE / 15 / 15 Pro Max
(portrait and landscape), iPad mini, iPad Pro 11", and iPad Pro 12.9 landscape:
no horizontal overflow, tap-to-place works, no control under 44px.

### Hard constraints

- **No `localStorage`, `sessionStorage`, or cookies.** Sessions are deliberately
  ephemeral: closing the tab clears everything.
- **No external requests.** No CDNs, no web fonts, no analytics, no images. It
  must work offline and from `file://` — which is why the bank is baked in
  rather than fetched.
- **`index.html` stays self-contained.** Do not split out the CSS or JS.
- **Python 3 standard library only.**
- Bank text is untrusted-ish input: everything goes through `esc()` or
  `textContent`. No `innerHTML` with raw bank strings.

### Copy rules

Sentence case, plain verbs, no exclamation marks. American spelling throughout,
in code, comments, and UI. Buttons say what happens: "Check answer", "Practice
what I missed". **Do not add a "you passed" line.** Microsoft scores its exams
out of 1,000 with 700 to pass, which is a scaled score and not a percentage of
questions; the green/red numeral is styling, not a verdict.

---

## Known data issues

Provenance: every question came from `pl-900.pdf`, a third-party exam dump, via
`build_questions.py`. Native PDF text supplied the stems, plain choices,
answers, and explanations. The interactive questions are pictures in that PDF,
so their answer areas were OCRed and the text boxes kept with coordinates. A
table of hand-transcribed answers (`VISUAL_ANSWER_OVERRIDES` in
`build_questions.py`) covers the answer areas whose marks OCR could not read.
The PDF is in the repo, so any claim here can be re-checked.

- **382 questions in, 381 baked.** One `drag_and_drop` question about Power BI
  Desktop vs Service is dropped: its answer area has five rows but the answer
  key records only three, so there is no honest way to pair them. Its stem and
  raw extraction are still in `questions.json`.
- **16 questions carry a build-generated `review` note.** Nine fell all the way
  back to a plain multi-select because their answer-area geometry could not be
  read as a table — several of those are pictures of app screenshots rather
  than tables, and their wording is rough. Seven are Yes/No questions where the
  number of statements and the number of recorded answers disagree; extra rows
  are shown but not graded, and the note says so.
- **OCR confusions are corrected mechanically**, not by reading: `Al` → `AI`,
  `Power Bl` → `Power BI`, `Ul` → `UI`, `PowerApps` → `Power Apps`, plus a few
  glyph substitutions. Wrong-looking option text that survives these is real
  OCR damage in the source.
- **Explanations were extracted from the same dump as the answers.** An
  explanation is *not* independent confirmation of its answer. Where the two
  disagree, trust neither until you have checked Microsoft Learn. Several
  explanations argue for an answer the `correct` field does not record — the
  Yes/No mismatches above are the visible cases.
- **Terminology differs between the banks, deliberately.** Full is left in the
  dump's original wording, which predates the renames — it still says Common
  Data Service (45 times), Power Virtual Agents (50), Microsoft Flow (10),
  entity, and field. Focused has been rewritten to the current names:
  Dataverse, Microsoft Copilot Studio, Power Automate, table, column, row, with
  the choices, answers, explanations, and the OCR text in `interaction`
  updated in step so the answer-area reconstruction still matches. Neither bank
  is wrong; Focused is the one that reads like the current exam.
- **Duplicates:** exact-normalized repeats are dropped by `dedupe`. Near
  duplicates — the same fact asked in different words — are kept. There are
  several; they are legitimate recall practice.
- **Focused is Full's questions, not extra ones.** 195 of its 230 stems are
  still character-for-character in `questions.json`, and the other 35 differ
  only by the renamed terms. Drilling both banks means seeing these questions
  twice, and a Focused score is not independent evidence on top of a Full
  score. 17 of its stems repeat inside the bank itself; they survive `dedupe`
  because their statements or answers differ, so they are distinct questions
  sharing a boilerplate stem. It builds clean: 230 in, 230 rendered, 9 flagged
  for review.
- **Two leftovers from the rename pass in Focused**, both cosmetic and neither
  in an answer: 11 occurrences of "a agent" where "an agent" is meant, and one
  surviving "Power Virtual Agents" in the explanation of the ticketing-app
  question. That one hid because the dump wrote it with narrow no-break spaces
  (`Power\u202fVirtual\u202fAgents`), so a plain search for the phrase misses
  it — `prose()` normalizes those to ordinary spaces at build time, which is
  why it shows up on the page but not in a grep of the JSON. Search the baked
  bank, not the source, when checking whether a term is really gone.

---

## Acceptance checklist

```bash
python3 build.py --check     # exits 0, reports the counts you expect
python3 build.py             # writes both files
git diff --stat              # MUST show questions.json AND index.html
```

Then, in the browser (`file://` is the real target):

- [ ] Practice, choice: right answer → green `+` gutter, "Correct", explanation shown
- [ ] Practice, wrong answer → red `−` on your pick, green `+` on the answer
- [ ] Yes/No: each row grades independently in the gutter; the answer you missed
      shows a dashed green ring; "not graded" rows are labeled
- [ ] Dropdown: every row must be set before "Check answer" enables; a wrong row
      prints `Answer: …` beneath the select
- [ ] Drag and drop: click-an-item-then-a-slot works; dragging works; clicking a
      filled slot clears it; items can be reused
- [ ] Ordering: placing an action in a second step removes it from the first;
      used actions dim in the pool
- [ ] Shuffle choices on → options move across runs and still grade right
- [ ] Test mode: no verdict, no answers, missed numbers listed at the end
- [ ] Mock: clock counts down, turns red at 10:00, auto-submits at zero
- [ ] "Practice what I missed" re-runs only the missed questions, in practice mode
- [ ] Results: topic meters sum to the session length; map cells jump to review
- [ ] Exam chrome: title strip, "Question N of M", timer chip and the fixed
      bottom bar appear on start and disappear on finish
- [ ] 375px wide: the answer column stacks under the prompt, the action bar
      buttons go full width, nothing overflows
- [ ] Reduced motion: no animation, score numeral appears at its final value
- [ ] Tabs: switching repoints the stats, the kind breakdown and the length
      chips; arrow keys move between tabs
- [ ] A session started from Focused contains only Focused questions, and the
      sticky bar and results header both say Focused
