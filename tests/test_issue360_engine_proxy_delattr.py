"""#360: the module's per-thread engine proxy forwards a delete to this
thread's engine.

pytest 9.2's monkeypatch records that the proxy itself has no ``_eval_expr``
and undoes ``setattr(de._engine, "_eval_expr", f)`` with ``delattr(proxy,
"_eval_expr")``. The proxy forwarded getattr and setattr only, so the delete
raised AttributeError on its slots and ``f`` stayed on the thread's engine,
shadowing the class for later tests (CI of the 0.9.150 tag:
tests/test_issue272_engine_proxy_isolation.py and a teardown error in
tests/test_issue186_shared_memo.py).

Expected values: by construction -- after the delete the engine has no
attribute of its own, and a class patch reaches it.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def test_a_delete_through_the_proxy_removes_the_engines_own_attribute():
    de._engine._eval_expr = lambda *a, **k: None
    assert "_eval_expr" in vars(de._engine._get())
    delattr(de._engine, "_eval_expr")
    assert "_eval_expr" not in vars(de._engine._get())


def test_deleting_an_attribute_the_engine_does_not_hold_raises():
    with pytest.raises(AttributeError):
        delattr(de._engine, "_no_such_attribute_360")


def test_after_the_delete_a_class_patch_reaches_the_engine(monkeypatch):
    de._engine._eval_expr = lambda *a, **k: None
    del de._engine._eval_expr
    seen = []
    real = de.DAXEngine._eval_expr

    def counting(self, expr, ctx, var_scope=None):
        seen.append(expr)
        return real(self, expr, ctx, var_scope)

    monkeypatch.setattr(de.DAXEngine, "_eval_expr", counting)
    got = de.evaluate_measures_batch(["m"], {"T": {"columns": ["v"], "rows": [[1], [2]]}},
                                     {"m": "SUM(T[v])"}, {})
    assert got == {"m": 3}
    assert "SUM(T[v])" in seen
