# Desktop probes for the open cases (issue #198)

**Status 2026-10-20: all run** (`answers_pending.tsv`; follow-ups R, S, T in
`queries_r.txt` / `queries_s.txt` / `queries_t.txt` and their answers). Outcome:
Q1a-c, Q1d1, Q2, Q3, Q4, Q5, Q6, Q7b2, Q9, Q10 confirm the code; Q1d2 / Q1d3
errored (UNICHAR refuses lone surrogates; R1a-R1e ask it with whole
characters); Q7a changed the code (marks with nothing before them get a level
of their own, with R6); Q8 changed the find (the text is taken from the start
on, `?` never takes a lone mark, with R4 / R5 / S / T); Q7b1 cannot be followed
by any key and is a strict xfail. Details: the experiment record's probe log.
The original list follows.


Not run: on 2026-10-19 the coordinator stopped all Power BI interaction while
the machine was in use. Each probe is one DAX expression, sent with
`probe.ps1` as `EVALUATE ROW("v", <expression>)` to the workspace engine of
the Power BI Desktop running on this machine (any model with one table, e.g.
the one-row model `PBIXBuilder` writes). `queries_pending.txt` holds the same
list in `probe.ps1`'s input format. Record the answers next to it as
`answers_pending.tsv`, with the Desktop version.

Until they run, these cases are **open**: the code keeps the answer named in
"code now" (the published table's, or the simplest rule consistent with the
pinned batteries), and nothing in the tests depends on it.

| id | DAX expression (shape) | settles | code now |
|---|---|---|---|
| Q1a | `IF(UNICHAR(20304) & UNICHAR(12293) = UNICHAR(20304) & UNICHAR(20304), 1, 0)` | Is the iteration mark U+3005 the character before it in comparison (spec 3.1.5.2.14 Repeat) or a character of its own? | its own character (b190 only shows it for SEARCH) |
| Q1b | `IF(UNICHAR(12354) & UNICHAR(12445) = UNICHAR(12354) & UNICHAR(12354), 1, 0)` | Same for the hiragana iteration mark U+309D after a kana | its own character |
| Q1c | `IF(UNICHAR(12459) & UNICHAR(12540) = UNICHAR(12459) & UNICHAR(12450), 1, 0)` | Cho-on U+30FC after a kana: the kana's vowel (spec) or its own? | its own character |
| Q1d | `IF(UNICHAR(12293) > UNICHAR(20013), 1, 0)`, `IF(UNICHAR(12293) > UNICHAR(55357) & UNICHAR(56832), 1, 0)`, `IF(UNICHAR(12293) > UNICHAR(56319), 1, 0)` | Where the repeat / cho-on marks sort: after everything (the spec's invalid weight FF FF) or elsewhere | after everything |
| Q2a | `IFERROR(SEARCH("?" & UNICHAR(769), "e"), -1)` | A mark right after `?` in SEARCH's pattern: ignored, or must the element carry it? | ignored (would give 1) |
| Q2b | `IFERROR(SEARCH("?" & UNICHAR(769), "e" & UNICHAR(768)), -1)` | Same, with a different mark on the text | ignored (1) |
| Q3a | `IF(UNICHAR(1611) = UNICHAR(849), 1, 0)` | Does fathatan U+064B weigh as U+0351, as U+064C-U+0651 weigh as U+0352-U+0357? | no: table DW 220 |
| Q3b | `IF("a" & UNICHAR(1618) = "a" & UNICHAR(8405), 1, 0)` | Sukun U+0652 = U+20D5 (both DW 226 in the table) | equal |
| Q3c | `IF("a" & UNICHAR(1612) < "a" & UNICHAR(851), 1, 0)` and `IF("a" & UNICHAR(1612) = "a" & UNICHAR(850), 1, 0)` | Confirms the Arabic harakat correction in context (after a letter) | U+064C = U+0352 |
| Q4 | `IF(UNICHAR(64965) = UNICHAR(1589) & UNICHAR(1605) & UNICHAR(1605), 1, 0)` and `IF(UNICHAR(64965) = UNICHAR(1589) & UNICHAR(1581) & UNICHAR(1605), 1, 0)` | U+FDC5: sad meem meem (its name; Desktop's U+FDC5 = U+FD66, b187) or the table's sad hah meem | sad meem meem |
| Q5 | `IF(UNICHAR(64294) = UNICHAR(1501), 1, 0)`, `IF(UNICHAR(64294) = UNICHAR(1502), 1, 0)` | U+FB26's case weight (table 19; b181 says = U+05DD) | CW 3 |
| Q6a | `IF(UNICHAR(4352) & UNICHAR(4449) = UNICHAR(44032), 1, 0)` | Leading + vowel jamo vs the composed syllable U+AC00 (spec MapOldHangulSortKey / Windows composes; 0.9.137 compared jamo one by one) | apart |
| Q6b | `IF(UNICHAR(4352) & UNICHAR(4449) < UNICHAR(44033), 1, 0)` | Order of a jamo sequence among syllables | by jamo weights |
| Q7a | `IF(UNICHAR(769) & "e" > "e" & UNICHAR(769) & UNICHAR(769), 1, 0)` | Order of a text that starts with a mark (b189 puts U+0301 + "e" after every accented e; the key does not) | key puts it before "é" -- open |
| Q7b | `IF("e" & UNICHAR(769) > "e-", 1, 0)` with `IF(UNICHAR(233) > "e-", 1, 0)` | Decomposed vs precomposed against trailing punctuation (b189: "é" < "e-" < "e" + U+0301) | both before "e-" -- open |
| Q8 | `IFERROR(FIND(UNICHAR(769), "e" & UNICHAR(769), 2), -1)` and `IFERROR(SEARCH("?", "e" & UNICHAR(769), 2), -1)` | A start inside an element (between a letter and its mark) | no element starts there: not found |
| Q9 | `IF(UNICHAR(1488) & "-" < UNICHAR(1499) & "-", 1, 0)` vs `IF(UNICHAR(1488) < UNICHAR(1499), 1, 0)` | Where case weights (kept for Hebrew) rank against trailing punctuation | trailing punctuation first, then accents, then case weights, then the word sort |
| Q10 | `IF(UNICHAR(12354) & "-" < UNICHAR(12353), 1, 0)` | Small kana vs trailing punctuation (the kana case weight level) | as Q9: "あ-" > "ぁ" |

Each answer either confirms the code (record it in the experiment record and
cite the probe id in the code) or changes a rule (change it, cite the probe,
add the case to the tests).
