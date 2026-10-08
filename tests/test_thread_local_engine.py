"""Evaluations on different threads don't share engine state.

The module-level DAXEngine carried per-evaluation state (deadline, depth,
ALLSELECTED snapshots, unsupported functions, errors) that pbix_evaluate_dax
reads back after each call, so concurrent calls corrupted each other and a host
had to serialise them. Each thread now has its own engine.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from pbix_mcp import server
from pbix_mcp.builder import PBIXBuilder
from pbix_mcp.dax import engine as de


def test_the_flag_and_one_engine_per_thread():
    assert de.THREAD_SAFE_EVALUATION is True
    with ThreadPoolExecutor(4) as ex:
        ids = set(ex.map(lambda _: id(de._engine._get()), range(16)))
    assert len(ids) > 1


def test_concurrent_calls_answer_as_sequential_ones(tmp_path):
    b = PBIXBuilder("Threads")
    b.add_table("T", [{"name": "k", "data_type": "Int64"}, {"name": "v", "data_type": "Double"}],
                rows=[{"k": i % 7, "v": float(i)} for i in range(20000)])
    b.add_measure("T", "Total", "SUM(T[v])")
    b.add_measure("T", "Share", "DIVIDE(SUM(T[v]), CALCULATE(SUM(T[v]), ALLSELECTED(T)))")
    b.add_measure("T", "Broken", "NOSUCHFUNCTION(1)")
    p = tmp_path / "threads.pbix"
    b.save(str(p))
    server.pbix_open(str(p), alias="threads")
    try:
        def call(k):
            measures = "Total,Share" if k % 2 else "Total,Share,Broken"
            r = json.loads(server.pbix_evaluate_dax("threads", measures, filter_context=json.dumps({"T.k": [k % 7]}),
                                                    apply_default_filters=False))
            return k, {x["name"]: (x["status"], x.get("value")) for x in r["results"]}, r.get("warnings", [])
        sequential = {k: call(k) for k in range(28)}
        with ThreadPoolExecutor(16) as ex:
            concurrent = {k: res for k, *res in ex.map(call, list(range(28)) * 6)}
        for k, (results, warnings) in concurrent.items():
            assert results == sequential[k][1], k
            # A thread whose call had no broken measure never sees another thread's unsupported function.
            assert bool(warnings) == (k % 2 == 0), (k, warnings)
    finally:
        server.pbix_close("threads")
