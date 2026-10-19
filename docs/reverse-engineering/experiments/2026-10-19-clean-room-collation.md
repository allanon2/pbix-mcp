# 2026-10-19 — clean-room collation: text comparison, order and find re-derived (issue #198)

## Why

From 0.9.138 (`aab2cd2`) pbix-mcp's text collation (`src/pbix_mcp/dax/collation.py`,
`_collation_data.py`) and the find helpers in `engine.py` (`_wild_*`, `_find_text`)
were built with material the clean-room policy (`docs/legal-and-cleanroom.md`,
`docs/reverse-engineering/methodology.md`) does not allow as a source. Issue #198
replaces them with code derived only from published Microsoft material and
black-box answers of Power BI Desktop.

## How this was done, honestly

An AI session (Claude, for ComposeBI's owner) did the work on 2026-10-19 on branch
`clean-room-collation-198` (from 0.9.143, `89898fb`), as the implementer of a
clean-room brief (`2026-10-19-clean-room-collation/CLEAN_ROOM_BRIEF.md`, as
it was handed over) written by a separate coordinator session. The
implementer started with no context but the brief.

- **Starting state.** The coordinator had put 0.9.137's `collation.py` and
  `_collation_data.py` (`b75699d`, before the forbidden work) in the working
  tree, and 0.9.137's block from `_WILD_ANY =` to `_MONTHS =` in `engine.py`.
  The coordinator had seen HEAD's version (it wrote the brief from HEAD's call
  sites), so it knew the names HEAD uses -- `collation.search(tokens, text,
  start0, ignore_case)`, `collation.ANY` / `collation.RUN` -- and passed those
  names on; nothing else of HEAD's collation came through it. The coordinator
  itself also saw, in one grep listing, the names and one-line comments of
  HEAD's module-level constants in `collation.py`; it read no function body and
  passed none of that on.
- **A pointer from the tests.** Before analysing, the implementer read the
  module docstring of `tests/test_issue185_text_collation.py`, which says case
  and width drop out "only in the cased scripts (up to Coptic ...)". That
  docstring was written in 0.9.138, with the forbidden code, so the pointer may
  carry binary-derived knowledge. The rule was then checked against the
  batteries alone (see the case-level row below): every case-only pair of SM
  7-20 is equal, SM 40 (Hebrew) and up have pairs apart, and no SM 21-39 occurs
  in the table, so the observable behaviour is settled by the data; only the
  cut-off's exact place between 20 and 40 is not. The coordinator then reduced
  the 0.9.138 prose of `test_issue157`, `test_issue180` and `test_issue185`
  (docstrings) to the Desktop answers and their battery ids, replaced
  `engine.py` `_text_key`'s claim about the table's origin, and added a
  correction note to the 0.9.138 entry of `docs/openbi-findings-ledger.md`.
- **Not opened:** `collation.py` / `_collation_data.py` at 0.9.138 to HEAD (no
  `git show`, `git diff`, `git log -p` on them; no `git log` at all), the 0.9.138
  commit message and CHANGELOG entry, the findings-ledger entries for #180 /
  #185, the Desktop oracle toolkit and its generators, any Microsoft binary.
  No debugger or memory tool was used.
- **One accident.** The first baseline test run was made without `PYTHONPATH=src`
  and so imported the copy of pbix-mcp **0.9.139** installed in
  `C:\Python313\Lib\site-packages` -- which contains the forbidden collation.
  Its code was executed by that test run (850 passed), never opened or read;
  the run's numbers were discarded and every later run used `PYTHONPATH=src`.
  Anyone repeating this must do the same, or uninstall that copy.
- **Read and used:** the published weight table, [MS-UCODEREF] 3.1.5.2
  (fetched from learn.microsoft.com, the sections listed below), the tests
  (which pin Desktop's answers), 0.9.137's code (its word-sort rule and
  `_wild_tokens` / `_wild_search` shape), HEAD's `engine.py` outside the
  excluded block.
- **Desktop probes.** The brief said Desktop was running; it was not. The
  session started the installed Power BI Desktop (2.152.882.0, engine
  17.0.61.24), opened a one-table model written by `PBIXBuilder`, and checked
  the probe channel (`probe.ps1`; `probes_p0.txt` / `answers_p0.tsv`: `1+1` is
  2, `"a" = "A"` is TRUE). Then the coordinator stopped all Power BI use (the
  machine was needed) and the open cases were written down as probes
  (`pending_probes.md`). On 2026-10-20, with Power BI allowed again, the
  session started Desktop once more (same version, the same one-table model)
  and ran them and their follow-ups -- DAX queries only, `EVALUATE ROW` each,
  through `probe.ps1` (probe log below) -- then closed the Desktop and msmdsrv
  it had started (by PID and start time).

## Evidence used

1. **Microsoft's "Windows Server 2008 Sorting Weight Table"** (download id 10921,
   sha256 `b3b18105...0c75`), not committed. `gen_collation_data.py` reads three
   sections -- SORTKEY DEFAULT (58,093 code points: SM, AW, DW, CW), EXPANSION
   (711), COMPATIBILITY (952) -- and writes `_collation_data.py` (65,536 records
   of four bytes, lzma, hex; 710 expansions). en-US has no EXCEPTION or
   COMPRESSION table of its own, so those sections do not apply. The
   coordinator re-ran the generator on the downloaded table and got the
   committed `_collation_data.py` byte for byte.
2. **[MS-UCODEREF] 3.1.5.2**: 3.1.5.2.2 CompareSortKey, 3.1.5.2.3 (the table's
   fields), 3.1.5.2.4 GetWindowsSortKey (levels, script-member constants, case
   weight bits, trailing minimum weights), 3.1.5.2.9 GetCharacterWeights,
   3.1.5.2.10 / .11 expansions, 3.1.5.2.14 SpecialCaseHandler (nonspacing
   marks, punctuation, kana, jamo, Extension A), 3.1.5.2.15.
3. **Desktop's answers pinned in the tests**: `test_issue185_text_collation.py`
   (b181 2,938 pairs =; b185 1,093 pairs = and <; b186 ORDER BY of every BMP
   character, sampled; b187 neighbours =; b189 marks), `test_issue157_text_collation.py`
   (b156 / b157 / b157g orders, pairs), `test_issue180_linguistic_search.py`
   (b190), `test_issue169_search_wildcards.py` and the other text tests
   (b166-b175).

## Findings per rule, and the code

`src/pbix_mcp/dax/collation.py` cites the evidence at each rule.

| rule | source | check |
|---|---|---|
| Weights per UTF-16 code unit from DEFAULT; an unlisted unit is unsortable (weighs nothing): U+1E9E sorts first, "\u00ad" is skipped | table; 3.1.5.2.9 | b157, b181, b190 |
| A code point not in DEFAULT but in COMPATIBILITY takes its target's weights (the pseudocode omits this section) | table header | b181: U+037E = ";", U+1F71 = U+03AC |
| Expansions (æ = ae, ß = ss, ﬃ = ffi, twice at most) | 3.1.5.2.10 / .11 | b157, b181 |
| Primary level: (SM, AW); kana (SM 3) as (34, AW); jamo (SM 4) as (AW, DW) -- their real script member and weight; CJK (SM 5) as (254, 255), (AW, DW); beyond the BMP by the two surrogates | 3.1.5.2.14 | b186 ORDER BY sample: kana between Coptic and Arabic, U+1135 among the SM 217 syllables, 😀 < 가 < 中 |
| Diacritic level: a nonspacing mark adds to the DW before it, across unsortable characters; trailing DW 2 dropped | 3.1.5.2.14, 3.1.5.2.4 | b189 (all = answers) |
| A mark with nothing before it weighs on a level of its own, after trailing punctuation and before the accents | probes | Q7a, R6a-R6d: U+0301 + "e" > "e" + U+0308 + U+0308, < "f", < "e-", < "e" + U+0301 + "-" |
| Level order: primary, trailing punctuation, marks with nothing before them, accents, case, word sort | probes | R2a-R2c (U+05DA + "-" > U+05DB, U+3041 + "-" > U+3042), R3a-R3c (U+05DA + U+05BC > U+05DB, U+3041 + U+3099 > U+3042), Q9, Q10 |
| Punctuation (SM 6) weighs by position only, after the other levels | 3.1.5.2.14 | b157 ("coop" < "co-op" < "cop") |
| ... except at the end of a text (0.9.137's rule, from black-box b157f / b157g) | 0.9.137 | ORDER157G |
| A mark right after punctuation goes with the punctuation | new | b189: "é-" <> "e-" + U+0301, "-" + U+0301 <> U+0301 + "-" |
| Case level: ignored for SM 7-20 (symbols, digits, Latin, Greek, Cyrillic, Armenian, Georgian, Glagolitic, Coptic); elsewhere only the width and kana bits drop out | fitted to the batteries | 3,052 CW-only pairs (batteries overlap): all SM 7-20 pairs equal; Hebrew finals, Tibetan, U+2135, small kana, final jamo apart; hiragana = katakana = halfwidth |
| Repeat / cho-on kana (SM 3, AW 0 / 1): one weight of their own (the spec's invalid weight FF FF), not the character before | spec's "nothing before" case | b187 (all equal alone), b190; Q1a-Q1c ("佐々" <> "佐佐", "あゝ" <> "ああ", "カー" <> "カア"), Q1d1, R1a-R1e (U+3005 after 中, 😀, U+F0000, U+10FFFD, U+F8FF; = U+30FC) |
| Old-Hangul jamo sequences are not composed | 0.9.137 | b181 (U+11A8 <> U+1100); Q6a (U+1100 U+1161 <> U+AC00), Q6b (< U+AC01) |
| Find: text and pattern as elements (a character with its marks, an expansion whole, punctuation with its marks); unsortable characters in no element; `?` one element, `*` any run; SEARCH compares as `=` does, FIND with the whole case weight; a pattern of unsortables only is found nowhere, unsortables between wildcards drop out | b190 | all b166-b190 find tests |
| Marks right after `?` in SEARCH's pattern are ignored | probes | Q2a, Q2b: SEARCH("?" & U+0301, "e") and (..., "e" & U+0300) are 1 |
| The text is taken from the start on: a start between a letter and its mark leaves the mark an element of its own; `?` never takes a lone mark | probes | Q8a, Q8b, R4a-R4e, R5a-R5i |
| A find text starting with a mark: one mark alone is found at the start if the unit there or the next is a mark (any mark), never further on; two or more marks never; marks before more drop out of SEARCH, and FIND finds the rest only where a unit from the start on precedes it or a non-mark unit follows it | probes | S1-S128 (FIND / SEARCH x four find texts x eight texts x starts), T1-T23 |

**Corrections to the table** (each a pair Desktop answers, the table cannot
explain, and the smallest change that does):

- Arabic harakat U+064C-U+0651 and U+0670 weigh as U+0352-U+0357 and U+0349
  (b187 pairs; the Thaana signs U+07AB-U+07AF and U+1361 then equal the
  two-haraka ligatures U+FC5E-U+FC63 by the sums: 86 + 91 = 177 ...).
- An expansion of a space and nonspacing marks (U+FE70-U+FE7F, U+FC5E-U+FC63)
  weighs as the marks alone (b187: U+FE70 = U+064B = U+FE71).
- U+FB26 has CW 3 like the other wide Hebrew letters, not 19 (b181: = U+05DD,
  <> U+05DE; confirmed by Q5a, Q5b).
- U+FDC5 expands as U+FD66 (sad meem meem, as its name in the table says; the
  table gives sad hah meem) (b187: U+FDC5 = U+FD66; Q4a: = sad meem meem,
  Q4b: <> sad hah meem).
- The Arabic correction is confirmed after a letter (Q3c1, Q3c2); U+064B keeps
  the table's weight (Q3a: <> U+0351) and U+0652 = U+20D5 as the table has it
  (Q3b).

## Probe log

Power BI Desktop 2.152.882.0 (26.03), engine 17.0.61.24, en-US model, one
`EVALUATE ROW("v", <expression>)` per probe over ADOMD (`probe.ps1`), run
2026-10-20. Queries and answers, as sent and as printed:

| file | probes | what |
|---|---|---|
| `probes_p0.txt` / `answers_p0.tsv` | p0, p1 | channel check (2026-10-19) |
| `queries_pending.txt` / `answers_pending.tsv` | Q1a-Q10 (26) | the open cases of `pending_probes.md` |
| `queries_r.txt` / `answers_r.tsv` | R1a-R6d (29) | follow-ups: surrogates for Q1d (UNICHAR refuses lone surrogates, so Q1d2 / Q1d3 errored and R1a-R1c use whole characters), level order, `?` and lone marks, starts inside an element, texts starting with a mark |
| `queries_s.txt` / `answers_s.tsv` | S1-S128 | find texts U+0301, U+0301 & "x", U+0301 & U+0301, "x" & U+0301 with FIND and SEARCH over eight texts and every start |
| `queries_t.txt` / `answers_t.tsv` | T1-T23 | which texts a lone mark finds; FIND with a leading mark |

All 204 answers are pinned in `tests/test_issue198_clean_room_probes.py`
(203 pass, Q7b1 is `xfail(strict=True)` -- see Open).

## Open

- **Q7b1 / Q7b2**: Desktop sorts "e" + U+0301 after "e-" and the precomposed
  U+00E9 before it, while it finds them equal (b189). No key can give both;
  the key keeps the equality and orders both before "e-". The test is
  `xfail(strict=True)`. Of b189's 3,025 `<` answers 60 differ from the key, all
  of this kind (decomposed letters against trailing punctuation); before the
  probes it was 94 (the leading-mark ones are fixed by their own level).
- **Why** a find text that starts with a mark behaves as it does (S / T): the
  rules are read off the answers; no deeper model was found, and they cover
  only that case.
- The case-level rule's boundary (SM 20) is where the batteries' last cased
  script ends; no battery pair tells SM 21-39 (unused in the table) apart.

## Tests

Text tests (20 files: issues 107, 133, 156, 157, 160, 161, 163-165, 168, 169,
172, 176-178, 180, 181, 185, `test_calc_lookupvalue`, `test_sort_by_column`),
with `PYTHONPATH=src`:

- before (0.9.137 collation, find helpers missing): 111 failed, 739 passed, 10 skipped
- after: 850 passed, 10 skipped, 0 xfail
- new `tests/test_issue198_clean_room_probes.py`: 203 passed, 1 xfailed (strict)

Full suite before: 115 failed, 6548 passed, 166 skipped, 5 xfailed (the four
failures outside the text tests -- `test_dax_accuracy` SUBSTITUTE, two
`test_dax_conformance` TOCSV / TOJSON, one `test_rich_content` -- also came
from the missing find helpers; those files now pass: 452 passed, 43 skipped).

Full suite after (2026-10-20): 0 failed, 6866 passed, 166 skipped, 6 xfailed
-- the 115 failures fixed, 203 new passes and one new strict xfail from the
probe test file; no failure outside the text tests.

On the rewritten 0.9.144 with this work on top (0.9.145, 2026-10-20): 6953
passed, 166 skipped, 6 xfailed, 0 failed.

## History rewrite (2026-10-20)

So that no binary-derived material stays in the repository, the coordinator
rewrote every commit from 0.9.138 (`aab2cd2`) to 0.9.144 (`cd346ef`) with a
plumbing script (read each commit's tree, replace, `commit-tree` with the same
author, committer and dates) and released this work on top as 0.9.145. In
each rewritten commit:

- `collation.py` and `_collation_data.py` are this record's clean versions;
- `engine.py`'s block from after `_SPACE_RUNS =` to `_MONTHS =` (identical in
  all eight commits) is the clean block, and `_text_key`'s docstring the clean
  one;
- the 0.9.138 docstrings of `test_issue157`, `test_issue180` and
  `test_issue185` are the clean ones (their Desktop answers are unchanged);
- the 0.9.138 CHANGELOG entry, its findings-ledger sentence and the 0.9.138
  commit message are replaced by a notice.

Hashes in this record name the original commits. The rewrite maps them:

| release | original | rewritten |
|---|---|---|
| 0.9.138 | `aab2cd2` | `1220ec6` |
| 0.9.139 | `a503cd2` | `8680ac5` |
| 0.9.140 | `16a1b67` | `ca2ac3a` |
| 0.9.141 | `db514ab` | `112e0e7` |
| 0.9.142 | `5cc585f` | `8fb72b7` |
| 0.9.143 (PR #197) | `3bd9e92` | `a3122b3` |
| 0.9.143 | `89898fb` | `585210b` |
| 0.9.144 | `cd346ef` | `3f5ff10` |

Checks: each rewritten commit differs from the original only in those eight
files; each later commit's own change to `engine.py` / `collation.py` is the
original's, line for line; none of the 26 original versions of those files is
reachable from `main` or any tag; no rewritten commit or message holds the
tripwire strings the coordinator knew from the old code. The rewritten
0.9.138-0.9.144 commits carry code written later than their dates (this
record), and their own tests are not re-run.

Not reachable by a rewrite: forks and clones made earlier, GitHub's pull
request refs and cached commits (purged only by GitHub Support), and the
PyPI files of 0.9.138-0.9.144 (yanked).

## Files

- `src/pbix_mcp/dax/collation.py` -- new.
- `src/pbix_mcp/dax/_collation_data.py` -- generated.
- `src/pbix_mcp/dax/engine.py` -- `_wild_search` / `_find_text` call
  `collation.search`; `_wild_tokens` is 0.9.137's.
- `2026-10-19-clean-room-collation/CLEAN_ROOM_BRIEF.md`, `gen_collation_data.py`, `probe.ps1`,
  `pending_probes.md`, `probes_p0.txt` / `answers_p0.tsv`, `queries_pending.txt` /
  `answers_pending.tsv`, `queries_r.txt` / `answers_r.tsv`, `queries_s.txt` /
  `answers_s.tsv`, `queries_t.txt` / `answers_t.tsv`.
- `tests/test_issue198_clean_room_probes.py` -- new.
