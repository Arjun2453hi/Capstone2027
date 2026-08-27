# CLAUDE.md — DocumentParsing

Context for any Claude session (chat or Claude Code) working inside
`DocumentParsing/`. Read this before touching `pdf_parser.py`.

---

## 1. Where this fits in the larger project

Gap Detection system, 5 stages. This folder is **Step 1: Document
Structuring** — the foundation everything else sits on:
`Similiarity_gen`, `Global_Context`, and `Context_Window` all consume
this folder's output (`Slide.raw_text`, `.title`, `.bullets`) directly
or indirectly. A data-quality bug here silently propagates through
every downstream stage's numbers without any of them "seeing" an error
— which is exactly what happened here.

## 2. The bug this fix addresses

**Symptom:** two anchor slides picked by `Similiarity_gen`'s retriever
(slide_ids 270 and 138) looked wrong on manual inspection — the
retrieved "best match" slide didn't actually relate to its topic.

**Root cause:** the title-detection heuristic in `pdf_parser.py` is
"largest font at the top of the page = title." Real slide exports
almost always carry a repeated course/unit banner and a page-number
footer on every page. When that banner happens to render in a bigger
font than the slide's actual heading — common with university deck
templates — the banner wins the title slot instead of the real
heading, and the real title gets misclassified as a bullet. On most
slides this doesn't change much (the banner text is identical
everywhere, so it's mostly harmless noise), but it corrupts:
- **Embeddings** — a slide's `raw_text` starts with the banner instead
  of its actual heading, which is the single strongest signal a dense/
  TF-IDF embedding uses.
- **Title-weighted retrieval or reporting**, if built later, since the
  "title" field itself is just wrong.
- **Retrieval anchor quality** — explains slides 270 and 138 directly.

This was never caught earlier because the original test fixture
(`tests/fixtures/sample_deck.pdf`) didn't include a repeated banner —
it only tested clean, isolated slide content. **The fixture itself was
insufficiently realistic; that's part of what let this ship.**

## 3. The fix (already implemented, reference implementation below)

Two-pass parsing instead of one-pass-per-page:

1. **Pass 1** — extract every page's lines (text + font size) without
   doing any title/bullet classification yet.
2. **Detect boilerplate** — a line is "boilerplate" if it appears (after
   normalizing away digit runs, so `"Page 3 of 45"` and `"Page 4 of 45"`
   count as the same recurring line) on at least
   `max(BOILERPLATE_MIN_ABSOLUTE, BOILERPLATE_MIN_FRACTION * num_pages)`
   distinct pages. Counting unit is **distinct pages**, not raw
   occurrences — a line repeated twice on one page but nowhere else
   must not be flagged.
3. **Pass 2** — strip boilerplate lines from each page's line list,
   *then* run title/bullet detection on what's left.

**Order matters**: boilerplate must be stripped **before** title
detection runs, not after. Stripping after would already have let the
banner win the "largest font" check and misclassify the real title as
a bullet — the bug reproduces exactly if this order is reversed.

Reference implementation (from the reference sandbox build — adapt to
match whatever `pdf_parser.py` actually looks like in this repo right
now, since it may have diverged since this doc was written):

```python
BOILERPLATE_MIN_FRACTION = 0.30
BOILERPLATE_MIN_ABSOLUTE = 3
_DIGIT_RE = re.compile(r"\d+")

def _normalize_for_boilerplate(text: str) -> str:
    return _DIGIT_RE.sub("#", text.strip().lower())

def _detect_boilerplate_line_keys(all_page_lines, num_pages) -> set:
    page_counts = {}
    for page_lines in all_page_lines:
        seen_this_page = {_normalize_for_boilerplate(l["text"]) for l in page_lines}
        for key in seen_this_page:
            page_counts[key] = page_counts.get(key, 0) + 1
    threshold = max(BOILERPLATE_MIN_ABSOLUTE, int(BOILERPLATE_MIN_FRACTION * num_pages))
    return {key for key, count in page_counts.items() if count >= threshold}

# extract_deck(): collect all_page_lines first (pass 1), compute
# boilerplate_keys once for the whole deck, THEN loop pages again to
# filter clean_lines before _split_title_and_bullets() (pass 2).
```

## 4. Non-negotiable requirements

- **Two-pass, not one-pass.** Boilerplate detection needs to see the
  whole deck before it can know what's "repeated" — don't try to
  detect it per-page in a single loop.
- **Digit-normalize before counting.** Page-number footers change per
  page; exact-string matching alone will miss them entirely and defeat
  the whole point of this fix.
- **Threshold must have both a fraction and an absolute floor.**
  Fraction alone breaks on small decks (a real repeated line only needs
  to hit 2 pages out of 5 to look like 40%, but that's not enough
  evidence). Absolute floor alone breaks on huge decks (3 occurrences
  out of 500 pages is nothing). Keep both checks.
- **Don't over-strip.** A phrase that legitimately recurs a couple of
  times across genuinely different slides (e.g. "mocking" appearing in
  several unrelated bullets) must NOT be caught — this fix targets
  structural repetition (same line, most of the deck), not topical
  word repetition. If real content starts disappearing after this fix,
  the threshold is miscalibrated, not the concept.
- **Update the test fixture, don't just patch the code.** The fixture
  deck must include a realistic banner + page-number footer on every
  page — see `tests/fixtures/make_fixture.py` — otherwise this bug (or
  a regression of it) will silently pass tests again the same way it
  shipped the first time.

## 5. Testing — regression tests already written, reuse this pattern

Existing tests to bring over / confirm still pass (in
`tests/test_pdf_parser.py`):
- `test_repeated_banner_not_treated_as_title` — the banner text must
  never appear as any slide's `title`.
- `test_page_number_footer_not_leaked_into_bullets` — no bullet should
  start with the page-number pattern, across any slide.
- `test_real_titles_recovered_after_boilerplate_removed` — spot-checks
  that real titles come back correctly once boilerplate is out of the
  way.
- `test_legitimate_infrequent_repetition_is_kept` — confirms the fix
  isn't over-aggressive (see requirement above).

**After patching the real `pdf_parser.py` in this repo:**
1. Run the existing full suite first — confirm nothing regresses.
2. Re-run **Step 1's parse** on the real deck (`se-u2-slides.pdf`) and
   manually check slide_ids 270 and 138 specifically — print their
   `title`/`bullets` before and after the fix, side by side.
3. **Re-run every downstream stage** that consumes this output —
   `Similiarity_gen`'s retrieval report, `Global_Context`'s clustering,
   and `Context_Window`'s bundles — since all three were built on top
   of the contaminated parse. Report whether the retrieval anchors for
   270/138 (and any other suspicious ones) change after the fix, and
   whether clustering ARI or retrieval accuracy numbers move at all —
   they may not move much (the banner was identical noise added to
   *every* slide, which partially cancels out in relative comparisons),
   but this needs to be checked, not assumed.

## 6. Where this file goes

`DocumentParsing/CLAUDE.md` — same convention as `Similiarity_gen/`,
`Global_Context/`, and `Context_Window/`: the doc lives at the root of
the module folder it describes.

## 7. Prompt to run (paste this in)

```
Fix the boilerplate-artifact bug in DocumentParsing per CLAUDE.md.

Specifically:
1. Open pdf_parser.py and locate the current single-pass extract_deck().
2. Restructure it into two passes per Section 3: collect all pages'
   lines first, detect boilerplate line keys across the whole deck
   (digit-normalized, with both a fraction and absolute-count
   threshold per Section 4), then filter each page's lines before
   running title/bullet detection.
3. Update tests/fixtures/make_fixture.py to include a repeated banner
   (bigger font than the real title) and a page-number footer on every
   page, matching the reference fixture change — regenerate the
   fixture PDF.
4. Add the four regression tests from Section 5 to test_pdf_parser.py,
   plus confirm all pre-existing tests still pass.
5. Re-parse the real se-u2-slides.pdf and print slide 270 and slide
   138's title/bullets before vs. after the fix.
6. Re-run the Similiarity_gen retrieval report, Global_Context
   clustering, and Context_Window bundle build on the corrected parse,
   and report whether retrieval accuracy, clustering ARI, or the
   specific anchor slides for those two topics changed.
```