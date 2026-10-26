"""#272: undoing a monkeypatch of the module's engine left
the patched method's original on that thread's engine, shadowing the class.

tests/test_issue131_memo_shield.py::test_evaluated_once_not_once_per_row
failed when run after tests/test_issue233_evaluate_dax_batch.py (also after
the tests of issues #186 and #193). Those patch ``_engine._eval_expr`` -- the
per-thread proxy, which forwards setattr to this thread's DAXEngine.
monkeypatch's undo read the old value through the proxy (the engine's bound
method) and set it back, so the engine kept an instance attribute: a later
patch of DAXEngine._eval_expr never reached it, and the count it took was 0.

Expected values: by construction -- after the undo the engine has no
attribute of its own shadowing the class, and a class patch reaches it.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def test_undoing_a_patch_of_the_proxy_leaves_no_shadow():
    mp = pytest.MonkeyPatch()
    mp.setattr(de._engine, "_eval_expr", lambda *a, **k: None)
    assert "_eval_expr" in vars(de._engine._get())
    mp.undo()
    assert "_eval_expr" not in vars(de._engine._get())


def test_a_class_patch_reaches_the_engine_afterwards(monkeypatch):
    mp = pytest.MonkeyPatch()
    mp.setattr(de._engine, "_eval_expr", lambda *a, **k: None)
    mp.undo()
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
