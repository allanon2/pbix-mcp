"""Issue #88: evaluate_measures_smart's row-context simulation was not
deterministic across runs.

When a measure is BLANK and reads ISFILTERED / SELECTEDVALUE, the simulation
tries one value of the column. It took list(set(values))[0] -- and set order of
strings follows the per-process hash seed, so the same call answered
"Type: Incident" in one run and "Type: Request" in the next (IT Support's
"Filters Applied Values" measure). It now tries the first value in data order.
"""
import json
import os
import subprocess
import sys

import pytest

import pbix_mcp

pytestmark = pytest.mark.unit

CHILD = r"""
import json
from pbix_mcp.dax import engine as de
T = {"S": {"columns": ["Type", "V"],
           "rows": [["Problem", 1], ["Request", 2], ["Incident", 3], ["Change", 4],
                    ["Question", 5], ["Alert", 6]]}}
M = {"F": 'IF(ISFILTERED(S[Type]), "Type: " & CONCATENATEX(VALUES(S[Type]), S[Type], "-"))'}
print(json.dumps(de.evaluate_measures_smart(["F"], T, M, {})["F"]))
"""


def _run(seed: int) -> str:
    env = dict(os.environ, PYTHONHASHSEED=str(seed),
               PYTHONPATH=os.path.dirname(os.path.dirname(pbix_mcp.__file__)))
    out = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True,
                         text=True, timeout=120, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_simulated_value_does_not_depend_on_the_hash_seed():
    answers = {_run(seed) for seed in range(6)}
    # the first value in data order, whatever the seed
    assert answers == {"Type: Problem"}
