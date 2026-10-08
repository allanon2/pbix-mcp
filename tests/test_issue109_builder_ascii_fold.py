"""Issue #109: PBIXBuilder folds only what the column store folds -- ASCII
letters.

The #43 fold used str.casefold(), so 'Äpfel' / 'äpfel', 'Straße' / 'Strasse'
and the like became one value (the caller's data rewritten), where Power BI
Desktop 2.152's own import keeps them distinct and folds only 'Apple' /
'apple': of 14 names, the built model held 7 distinct values and Desktop's
refresh of the same rows 13. Now built and refreshed answer alike.
"""
from __future__ import annotations

import json
import warnings

import pytest

from pbix_mcp import server as S
from pbix_mcp.builder import PBIXBuilder

pytestmark = pytest.mark.unit

NAMES = ["Apple", "apple", "Äpfel", "äpfel", "Øl", "øl", "Éa", "éa", "Σίγμα", "σίγμα",
         "Дом", "дом", "Straße", "Strasse"]


def test_the_column_key_folds_ascii_letters_only():
    from pbix_mcp.formats.vertipaq_encoder import column_text_key

    assert column_text_key("APPLE") == column_text_key("apple")
    assert column_text_key("Äpfel") != column_text_key("äpfel")
    assert column_text_key("Straße") != column_text_key("Strasse")
    assert column_text_key("ÄPFEL") == column_text_key("Äpfel")    # the ASCII part folds


def test_built_values_are_desktops(tmp_path):
    b = PBIXBuilder("i109")
    b.add_table("F5", [{"name": "Name", "data_type": "String"}, {"name": "v", "data_type": "Int64"}],
                rows=[{"Name": n, "v": 2 ** i} for i, n in enumerate(NAMES)])
    b.add_measure("F5", "d", "COUNTROWS(VALUES(F5[Name]))")
    p = tmp_path / "i109.pbix"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        b.save(str(p))
    folds = [str(w.message) for w in caught if "case" in str(w.message)]
    assert len(folds) == 1 and "'Apple' / 'apple'" in folds[0] and "Straße" not in folds[0]
    alias = "i109_" + tmp_path.name[-6:]
    assert json.loads(S.pbix_open(str(p), alias))["success"]
    try:
        r = json.loads(S.pbix_evaluate_dax(alias, "d", apply_default_filters=False))
        assert r["results"][0]["value"] == 13            # 0.9.118: 7; Desktop's import: 13
        doctor = json.loads(S.pbix_doctor(alias))
        text = json.dumps(doctor)
        assert "case-colliding" not in text              # Äpfel / äpfel are legitimate
    finally:
        S.pbix_close(alias)
