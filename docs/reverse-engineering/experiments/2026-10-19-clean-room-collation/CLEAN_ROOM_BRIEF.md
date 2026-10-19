# Clean-room brief: re-derive pbix-mcp's text collation (issue #198)

This brief contains only permitted material. It is everything the implementer receives.

## Repository

- Checkout: `E:\clean_room_fix\repo`, branch `clean-room-collation-198` (from 0.9.143, `89898fb`).
- Policy: `docs/legal-and-cleanroom.md`, `CONTRIBUTING.md`, `docs/reverse-engineering/methodology.md`.
- The record format to follow: `docs/reverse-engineering/experiments/2026-10-19-clean-room-remediation.md` (#197).

## Permitted sources

1. Microsoft's published **Windows Server 2008 Sorting Weight Table**, downloaded from
   <https://www.microsoft.com/download/details.aspx?id=10921> (file
   `Windows Server 2008 Sorting Weight Table.txt`, sha256
   `b3b18105b03ff6f75c0efae9580a0a16a8b14a58cbb79e644f49067dbc2b0c75`), at
   `E:\clean_room_fix\published_swt\`. Read-only. Do not commit the file; build data from it with a script that is committed.
2. **[MS-UCODEREF]** (Microsoft Open Specifications), in particular 3.1.5.2 (sort keys / string comparison).
3. **Black-box Desktop answers** already pinned in `tests/test_issue185_text_collation.py` and
   `tests/test_issue180_linguistic_search.py` (batteries b181-b190), and in the other text tests
   (`tests/test_issue157_text_collation.py`, `test_issue169_search_wildcards.py`, ...).
4. **New black-box probes**: DAX queries over ADOMD to the workspace engine of the Power BI Desktop
   that is running on this machine, reading only the query results. Record each probe (query, answer, Desktop version).
5. The repository's own code at **0.9.137 (`b75699d`)** and earlier, which predates the binary-derived work.

## Forbidden (do not open, run, read, or search)

- `src/pbix_mcp/dax/collation.py` and `src/pbix_mcp/dax/_collation_data.py` as they are at 0.9.138 (`aab2cd2`) through HEAD.
- In HEAD's `src/pbix_mcp/dax/engine.py`: the bodies of the block from `_WILD_ANY = ` up to `_MONTHS = `
  (`_wild_tokens`, `_wild_regex`, `_wild_search`, `_wild_find`, `_find_text`).
- The 0.9.138 commit message, its `CHANGELOG.md` entry, and the `docs/openbi-findings-ledger.md` entries for #180 / #185.
- Any Microsoft binary (`msmdsrv.exe`, Desktop DLLs): no disassembly, no debugger, no memory reads. The
  `donx64mcp-dbg` / `mcp-debugger` tools are off-limits for this task.
- Any "Desktop oracle toolkit" or emulator script, and `gen_collation_v100.py` / `collation_v100_corrections.json`.

If you hit forbidden material by accident, stop, say what you saw, and record it.

## Starting state (already done by the coordinator)

1. `collation.py` and `_collation_data.py` in the working tree are 0.9.137's (`b75699d`): clean, readable.
2. In `engine.py`, the block from `_WILD_ANY = ` to `_MONTHS = ` is 0.9.137's (lines ~741-808): clean, readable.
   The rest of `engine.py` is HEAD's and readable. The FIND, SUBSTITUTE, SEARCH, CONTAINSSTRING and
   CONTAINSSTRINGEXACT callers (around lines 14390-14510) still call `_find_text` and `_wild_search(..., ignore_case)`,
   which no longer exist. Write the find helpers yourself.
3. Never restore or read the forbidden versions. `git diff HEAD` on those files and the 0.9.138 commit are forbidden.

## Interface to keep (taken from call sites)

- `collation.sort_key(s) -> tuple`: order is Desktop's; equality is DAX text equality in expressions.
- `collation.column_order_key(s)`: used by `builder.py` and `formats/vertipaq_encoder.py` for a built column's hierarchy order.
- `collation.compare(a, b) -> int`, `collation.equal(a, b) -> bool`.
- A find routine used by SEARCH (wildcards `?` / `*` / `~`, case-insensitive), FIND (exact),
  CONTAINSSTRING, CONTAINSSTRINGEXACT and SUBSTITUTE, returning a 0-based start or -1. HEAD's engine names
  it `collation.search(tokens, text, start0, ignore_case)`, with `collation.ANY` / `collation.RUN` as the wildcard
  tokens. You may keep or change that shape.

## Method

1. Build the weight data from the published table plus MS-UCODEREF's algorithm only.
2. Run the batteries. For each case the table and the spec do not explain, settle it with a new Desktop probe.
   If no probe can settle it, keep the published table's answer and mark the test `xfail(strict=True)` with the reason.
3. No value may come from anywhere else. Every special case in the code cites its evidence (table line, spec section, or probe id).

## Deliverables

- New `collation.py`, `_collation_data.py` (generated), and the generator script under
  `docs/reverse-engineering/experiments/2026-10-19-clean-room-collation/`.
- Probe scripts and their recorded answers in the same folder.
- `docs/reverse-engineering/experiments/2026-10-19-clean-room-collation.md`: why, how it was done honestly
  (including that the coordinator saw HEAD's constant names), evidence, findings per rule, open items.
- Tests pass, or are `xfail(strict=True)` with stated evidence. Report pass/fail counts before and after.
- Do not commit or push. Leave the changes in the working tree for review.
