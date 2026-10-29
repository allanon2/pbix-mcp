"""Python.Execute in pbix_mcp.m: a script step's M side, as a Desktop 2.157 refresh showed it (pivot-studio #102's
probes). The expected values are Desktop's: the CSV a refresh handed the script for each input, and each pandas
output as the refresh read it back. Running the script is the caller's runner; here a fake one stands in."""
import datetime as dt
import re

import pytest

from pbix_mcp.m import MError, ScriptNotRun, evaluate, script_key
from pbix_mcp.m.python import from_csv, outputs, to_csv, wrap_script

# The input tables of the probes, and the CSV Desktop wrote for each.
IN_TYPES = ('#table(type table [i = Int64.Type, n = number, c = Currency.Type, p = Percentage.Type, t = text, '
            'l = logical, d = date, dt = datetime, dz = datetimezone, tm = time, du = duration], '
            '{{1, 1.5, 2.25, 0.1, "x", true, #date(2026, 1, 5), #datetime(2026, 1, 5, 9, 30, 0.5), '
            '#datetimezone(2026, 1, 5, 9, 30, 0, 11, 0), #time(9, 30, 0), #duration(1, 2, 0, 0)}, '
            '{null, null, null, null, null, null, null, null, null, null, null}})')
IN_TEXT = ('#table(type table [t = text], {{"a,b"}, {"say ""hi"""}, {"two#(cr,lf)lines"}, {" pad "}, {""}, '
           '{"NA"}, {"null"}, {"nan"}, {"007"}, {"1e3"}, {"True"}, {"naïve €"}, {"it\'s"}, {"tab#(tab)here"}})')
IN_NUMBERS = ('#table(type table [n = number, i = Int64.Type], {{0.1 + 0.2, 9007199254740993}, {1e21, -5}, '
              '{1e-7, 0}, {123456789.125, 2147483648}, {-0.0, null}})')
IN_ANY = '#table({"a"}, {{1}, {"x"}, {#date(2026, 1, 5)}, {null}, {true}})'
DESKTOP_IN = {
    IN_TYPES: 'i,n,c,p,t,l,d,dt,dz,tm,du\r\n1,1.5,Microsoft.OleDb.Currency,0.1,x,True,Microsoft.OleDb.Date,'
              '2026-01-05T09:30:00.5000000,01/05/2026 09:30:00 +11:00,Microsoft.OleDb.Time,1.02:00:00\r\n'
              ',,,,,,,,,,\r\n',
    IN_TEXT: 't\r\n"a,b"\r\n"say ""hi"""\r\n"two\r\nlines"\r\n pad \r\n\r\nNA\r\nnull\r\nnan\r\n007\r\n1e3\r\nTrue\r\n'
             'naïve €\r\n"it\'s"\r\ntab\there\r\n',
    IN_NUMBERS: 'n,i\r\n0.30000000000000004,9007199254740993\r\n1E+21,-5\r\n1E-07,0\r\n123456789.125,2147483648\r\n'
                '0,\r\n',
    IN_ANY: 'a\r\n1\r\nx\r\nMicrosoft.OleDb.Date\r\n\r\nTrue\r\n',
}

# What pandas 3.1 wrote for some outputs, and how Desktop read each back: (columns, rows).
PANDAS_OUT = {
    "o_basic": (b"i,f,b,s\n1,1.5,True,x\n2,2.0,False,y\n3,-0.0,True,z\n",
                (["i", "f", "b", "s"], [["1", "1.5", "True", "x"], ["2", "2.0", "False", "y"],
                                        ["3", "-0.0", "True", "z"]])),
    "o_nulls": (b"i,s,f\n1.0,a,\n,,1.0\n3.0,,inf\n",
                (["i", "s", "f"], [["1.0", "a", ""], ["", "", "1.0"], ["3.0", "", "inf"]])),
    "o_colnames": (b"dup,dup,5\n1,2,3\n", (["dup", "dup_1", "5"], [["1", "2", "3"]])),
    "o_nocols": (b"\n", ([""], [])),
    "o_empty": (b"a,b\n", (["a", "b"], [])),
    "o_text": (b't\n"a,b"\n"say ""hi"""\n"two\r\nlines"\n pad \nna\xc3\xafve \xe2\x82\xac\n007\nNA\nTrue\n1e3\n'
               b"it's\ntab\there\n",
               (["t"], [["a,b"], ['say "hi"'], ["two\r\nlines"], [" pad "], ["naïve €"], ["007"], ["NA"], ["True"],
                        ["1e3"], ["it's"], ["tab\there"]])),
    "o_dates": (b"d,dt,tz,td\n2026-01-05,2026-01-05 09:30:00.000000,2026-01-05 09:30:00+11:00,1 days 02:00:00.000000\n"
                b"2026-03-30,2026-03-30 17:05:00.123456,2026-03-30 17:05:00+11:00,0 days 00:00:01.500000\n",
                (["d", "dt", "tz", "td"],
                 [["2026-01-05", "2026-01-05 09:30:00.000000", "2026-01-05 09:30:00+11:00", "1 days 02:00:00.000000"],
                  ["2026-03-30", "2026-03-30 17:05:00.123456", "2026-03-30 17:05:00+11:00",
                   "0 days 00:00:01.500000"]])),
}


@pytest.mark.parametrize("m", list(DESKTOP_IN), ids=["types", "text", "numbers", "any"])
def test_an_input_is_written_as_desktop_wrote_it(m):
    assert to_csv(evaluate(m)).decode("utf-8") == DESKTOP_IN[m]


@pytest.mark.parametrize("name", list(PANDAS_OUT))
def test_an_output_is_read_as_desktop_read_it(name):
    data, (columns, rows) = PANDAS_OUT[name]
    t = from_csv(data)
    assert (t.columns, t.rows) == (columns, rows)
    assert all(isinstance(v, str) for r in t.rows for v in r)


def test_crlf_output_reads_the_same():
    data, want = PANDAS_OUT["o_nulls"]
    t = from_csv(data.replace(b"\n", b"\r\n"))
    assert (t.columns, t.rows) == want


def _runner(outputs, seen=None):
    def run(script, inputs):
        if seen is not None:
            seen.append((script, inputs))
        return outputs
    return run


class TestPythonExecute:
    def test_outputs_are_a_navigation_table_in_ntfs_order(self):
        # Desktop listed the probes' outputs so: "written_by_script" before "_o_private" (by upper-cased name).
        names = ["_o_private", "written_by_script", "o_basic", "dataset", "probe_raw"]
        run = _runner({n: b"v\n1\n" for n in names})
        t = evaluate('Python.Execute("x = 1")', python=run)
        assert t.columns == ["Name", "Value"]
        assert [r[0] for r in t.rows] == ["dataset", "o_basic", "probe_raw", "written_by_script", "_o_private"]
        assert t.rows[0][1].rows == [["1"]]

    def test_a_step_selects_one_output(self):
        run = _runner({"out": PANDAS_OUT["o_basic"][0]})
        t = evaluate('Python.Execute("out = 1"){[Name="out"]}[Value]', python=run)
        assert t.columns == ["i", "f", "b", "s"] and t.rows[2] == ["3", "-0.0", "True", "z"]

    def test_the_runner_gets_the_script_and_each_argument_as_csv(self):
        seen = []
        evaluate(f'Python.Execute("out = dataset", [dataset = {IN_NUMBERS}])', python=_runner({}, seen))
        assert seen == [("out = dataset", {"dataset": DESKTOP_IN[IN_NUMBERS].encode("utf-8")})]

    def test_without_a_runner_the_step_is_not_run_and_no_try_absorbs_it(self):
        with pytest.raises(ScriptNotRun) as e:
            evaluate(f'try Python.Execute("out = dataset", [dataset = {IN_ANY}]) otherwise null')
        assert e.value.parameters == ["dataset"]
        assert e.value.key == script_key("out = dataset", ["dataset"])

    def test_a_runner_may_decline(self):
        def run(script, inputs):
            raise ScriptNotRun(script, list(inputs), "This script isn't approved.")
        with pytest.raises(ScriptNotRun, match="approved"):
            evaluate('Python.Execute("x = 1")', python=run)

    def test_approval_covers_the_text_and_the_parameter_names(self):
        assert script_key("x = 1", []) != script_key("x = 1", ["dataset"])
        assert script_key("x = 1", ["dataset"]) == script_key("x = 1", ["dataset"])
        assert script_key("x = 1", []) != script_key("x = 2", [])

    @pytest.mark.parametrize("m, message", [
        ('Python.Execute(1)', "script as text"),
        ('Python.Execute("x", [dataset = 1])', "must be a table"),
        ('Python.Execute("x", {1})', "record of tables"),
        ('Python.Execute("x", [dataset = #table({"a"}, {{{1}}})])', "list in a table argument"),
    ])
    def test_bad_arguments_are_errors(self, m, message):
        with pytest.raises(MError, match=message):
            evaluate(m, python=_runner({}))

    def test_a_script_error_is_an_m_error(self):
        from pbix_mcp.m.python import script_failed

        def run(script, inputs):
            raise script_failed("Traceback ...\nValueError: boom\n")
        assert evaluate('try Python.Execute("x") otherwise "caught"', python=run) == "caught"


class TestWrapper:
    def test_inputs_read_then_the_script_verbatim_then_every_dataframe_written(self):
        script = "from __future__ import annotations\nout = dataset\ns = 'a\u2028b'\ncwd = os.getcwd(); v = sys.version"
        text, files = wrap_script(script, "/tmp/run", ["dataset"])
        (file,) = files.values()
        assert list(files) == ["dataset"] and re.fullmatch(r"input_df_[0-9a-f-]{36}\.csv", file)
        lines = text.split("\n")
        assert lines[0] == "from __future__ import annotations"
        read = f"dataset = pandas.read_csv({file!r})"
        assert lines.index(read) < lines.index("out = dataset")
        assert "s = 'a\u2028b'" in lines                      # the script's text as given, not split on U+2028
        assert {"import os, pandas, matplotlib", "import sys", "sys.tracebacklimit = 0"} <= set(lines)
        assert lines.index("import os, pandas, matplotlib") < lines.index("cwd = os.getcwd(); v = sys.version")
        assert text.count("os.chdir('/tmp/run')") == 2
        assert "for key in _pbix_globals():" in lines
        compile(text, "<wrapped>", "exec")

    def test_outputs_are_every_csv_but_the_inputs(self):
        names = ["out.csv", "INPUT_DF_1.CSV", "notes.txt", "Upper.CSV", "_pbix_script.py"]
        assert outputs(names, {"dataset": "input_df_1.csv"}) == ["out.csv", "Upper.CSV"]


def test_ntfs_order():
    from pbix_mcp.m.python import _ntfs_order
    names = ["a", "a-b", "_o_private", "written_by_script", "ßa", "SSb", "o_basic"]
    assert sorted(names, key=_ntfs_order) == ["a-b", "a", "o_basic", "SSb", "written_by_script", "_o_private", "ßa"]


def test_dotnet_round_trip_numbers():
    from pbix_mcp.m.python import _dotnet_r, _timespan
    assert [_dotnet_r(x) for x in (0.1 + 0.2, 1e21, 1e-7, 123456789.125, -0.0, 1.5, 1e16, 2.0)] == \
        ["0.30000000000000004", "1E+21", "1E-07", "123456789.125", "0", "1.5", "1E+16", "2"]
    assert _timespan(dt.timedelta(days=1, hours=2)) == "1.02:00:00"
    assert _timespan(dt.timedelta(seconds=-1.5)) == "-00:00:01.5000000"
