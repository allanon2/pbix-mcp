"""Issue #106 (PR #97 by @allanon2): evaluations on different threads do not
share engine state.

The module-level DAXEngine carried per-evaluation state (deadline, depth,
ALLSELECTED snapshots, unsupported functions, errors) that pbix_evaluate_dax
reads back after each call, so two evaluations on different threads overwrote
each other's: under 8 threads, 264 of 336 answers differed from the same calls
made one at a time (an ALLSELECTED share of 1.0001 for 1.0). Each thread now
has its own engine.
"""
from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from pbix_mcp import server as S
from pbix_mcp.builder import PBIXBuilder
from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def test_each_thread_has_its_own_engine():
    seen: list = []          # the engines themselves: an ended thread's could be freed, its id reused
    lock = threading.Lock()

    def grab(_):
        with lock:
            seen.append(de._engine._get())

    threads = [threading.Thread(target=grab, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(e) for e in seen}) == 4
    assert de._engine._get() is de._engine._get()       # stable within a thread
    assert all(isinstance(e, de.DAXEngine) for e in seen)


def test_concurrent_calls_answer_as_sequential_ones(tmp_path):
    b = PBIXBuilder("i106")
    b.add_table("T", [{"name": "k", "data_type": "Int64"}, {"name": "v", "data_type": "Double"}],
                rows=[{"k": i % 7, "v": float(i)} for i in range(20000)])
    b.add_measure("T", "Total", "SUM(T[v])")
    b.add_measure("T", "Share", "DIVIDE(SUM(T[v]), CALCULATE(SUM(T[v]), ALLSELECTED(T)))")
    b.add_measure("T", "Broken", "NOSUCHFN(1)")
    p = tmp_path / "i106.pbix"
    b.save(str(p))
    alias = "i106_" + tmp_path.name[-6:]
    assert json.loads(S.pbix_open(str(p), alias))["success"]
    try:
        def call(k):
            ms = "Total,Share" if k % 2 else "Total,Share,Broken"
            r = json.loads(S.pbix_evaluate_dax(alias, ms, json.dumps({"T.k": [k % 7]}),
                                               apply_default_filters=False))
            return k, ({x["name"]: (x["status"], x.get("value")) for x in r["results"]},
                       r.get("warnings") or [])

        sequential = dict(call(k) for k in range(14))
        # Switch threads often, so the calls interleave on every run rather
        # than whenever one happens to outlast the default 5 ms slice.
        old_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-5)
        try:
            with ThreadPoolExecutor(8) as ex:
                concurrent = list(ex.map(call, list(range(14)) * 4))
        finally:
            sys.setswitchinterval(old_interval)
        bad = [(k, got) for k, got in concurrent if got != sequential[k]]
        assert not bad, f"{len(bad)} of {len(concurrent)} concurrent answers differ: {bad[:2]}"
    finally:
        S.pbix_close(alias)
