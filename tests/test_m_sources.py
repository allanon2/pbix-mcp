"""Tier 2 and 3 of pbix_mcp.m: the table functions a query's steps use, and local files (File.Contents,
Csv.Document, Excel.Workbook, Parquet.Document). Rules from Microsoft's function reference; what it leaves open
is refused (each such case is pinned here as an error until Desktop settles it)."""
import datetime as dt
import io
import json
import os
import tempfile
import re
import uuid
import zipfile

import pytest

from pbix_mcp.m import CellError, MError, UnmappedSource, evaluate, force_table


def ev(m, files=None, **names):
    return evaluate(m, files=files, resolve=lambda n: names[n])


def files_of(**contents):
    def read(path):
        if path not in contents:
            raise UnmappedSource(path)
        return contents[path]
    return read


class TestTableFunctions:
    T = '#table({"a", "b"}, {{1, "x-y-z"}, {2, "p-q"}})'

    def test_rename_remove_select(self):
        assert ev(f'Table.RenameColumns({self.T}, {{"a", "A"}})').columns == ["A", "b"]
        assert ev(f'Table.RenameColumns({self.T}, {{{{"a", "A"}}, {{"b", "B"}}}})').columns == ["A", "B"]
        assert ev(f'Table.RemoveColumns({self.T}, "a")').columns == ["b"]
        t = ev(f'Table.SelectColumns({self.T}, {{"b", "a"}})')
        assert t.columns == ["b", "a"] and t.rows[0] == ["x-y-z", 1]

    def test_missing_fields(self):
        with pytest.raises(MError, match="column 'z'"):
            ev(f'Table.RemoveColumns({self.T}, "z")')
        assert ev(f'Table.RemoveColumns({self.T}, "z", MissingField.Ignore)').columns == ["a", "b"]
        with pytest.raises(MError, match="field 'z'"):     # the reference's own example error
            ev(f'Table.SelectColumns({self.T}, {{"a", "z"}})')
        t = ev(f'Table.SelectColumns({self.T}, {{"a", "z"}}, MissingField.UseNull)')
        assert t.columns == ["a", "z"] and t.rows[0] == [1, None]
        assert ev(f'Table.RenameColumns({self.T}, {{"z", "Z"}}, MissingField.Ignore)').columns == ["a", "b"]
        with pytest.raises(MError, match="already exists"):
            ev(f'Table.RenameColumns({self.T}, {{"a", "b"}})')

    def test_add_column(self):
        t = ev(f'Table.AddColumn({self.T}, "c", each [a] * 10, type number)')
        assert t.columns == ["a", "b", "c"] and [r[2] for r in t.rows] == [10, 20] and t.types[2].kind == "number"
        t = ev(f'Table.AddColumn({self.T}, "d", each if [a] = 1 then error "bad" else [a])')
        assert isinstance(t.rows[0][2], CellError) and t.rows[1][2] == 2       # a failed cell keeps its row

    def test_before_delimiter(self):
        # The reference's examples.
        assert ev('Text.BeforeDelimiter("111-222-333", "-")') == "111"
        assert ev('Text.BeforeDelimiter("111-222-333", "-", 1)') == "111-222"
        assert ev('Text.BeforeDelimiter("111-222-333", "-", {1, RelativePosition.FromEnd})') == "111"
        # Not found: the whole text, as a Desktop refresh shows (pivot-studio #104).
        assert ev('Text.BeforeDelimiter("111", "-")') == "111"
        assert ev('Text.BeforeDelimiter("a-b-c", "-", 5)') == "a-b-c"

    def test_promote_headers(self):
        t = ev('Table.PromoteHeaders(#table({"Column1", "Column2", "Column3"}, {{"ID", 2024, null}, {1, 2, 3}}))')
        assert t.columns == ["ID", "2024", "Column3"] and t.rows == [[1, 2, 3]]
        # As a Desktop refresh names them (pivot-studio #104): empty stays empty, a repeat gets "_<n>" from one
        # counter, a number its Text.From, and PromoteAllScalars writes the rest as Text.From does.
        assert ev('Table.PromoteHeaders(#table(3, {{"a", "", "b"}, {1, 2, 3}}))').columns == ["a", "", "b"]
        assert ev('Table.PromoteHeaders(#table(4, {{"a", "a", "b", "a"}, {1, 2, 3, 4}}))').columns == [
            "a", "a_1", "b", "a_2"]
        assert ev('Table.PromoteHeaders(#table(3, {{"a", "a", "a_1"}, {1, 2, 3}}))').columns == ["a", "a_1", "a_1_2"]
        assert ev('Table.PromoteHeaders(#table(3, {{"Column3", "x", null}, {1, 2, 3}}))').columns == [
            "Column3", "x", "Column3_1"]
        assert ev('Table.PromoteHeaders(#table(2, {{1, 2.5}, {1, 2}}))').columns == ["1", "2.5"]
        assert ev('Table.PromoteHeaders(#table(2, {{true, #date(2015, 11, 20)}, {1, 2}}))').columns == [
            "Column1", "Column2"]
        assert ev('Table.PromoteHeaders(#table(3, {{true, #date(2015, 11, 20), #datetime(2015, 11, 20, 10, 59, 0)}, '
                  '{1, 2, 3}}), [PromoteAllScalars=true])').columns == ["true", "11/20/2015", "11/20/2015 10:59:00 AM"]


class TestFiles:
    def test_without_a_mapping_a_file_is_unmapped(self):
        with pytest.raises(UnmappedSource) as e:
            ev('File.Contents("C:\\data\\x.csv")')
        assert e.value.path == "C:\\data\\x.csv" and e.value.reason == "DataSource.NotFound"

    def test_the_mapping_reads_it(self):
        assert ev('File.Contents("C:\\x")', files_of(**{"C:\\x": b"abc"})) == b"abc"


class TestCsv:
    def csv(self, data, options="", **kw):
        return ev(f'Csv.Document(File.Contents("f"){options})', files_of(f=data))

    def test_quoted_values(self):
        t = self.csv(b'a,"b, c","d ""e"""\r\n1,"two\nlines",3\n')
        assert t.columns == ["Column1", "Column2", "Column3"]
        assert t.rows == [["a", "b, c", 'd "e"'], ["1", "two\nlines", "3"]]

    def test_options_and_columns(self):
        t = self.csv(b"x;y\n1;2\n", ', [Delimiter=";", Columns=2, Encoding=65001, QuoteStyle=QuoteStyle.Csv]')
        assert t.rows == [["x", "y"], ["1", "2"]]
        # Extra values are dropped, whatever ExtraValues says, as a Desktop refresh shows (pivot-studio #104).
        for extra in ("", ", ExtraValues=ExtraValues.Error", ", ExtraValues=ExtraValues.List"):
            t = self.csv(b"x,y,z\n1,2,3\n", f', [Columns=2{extra}]')
            assert t.rows == [["x", "y"], ["1", "2"]]

    def test_encodings_and_byte_order_marks(self):
        assert self.csv("é,ü\n".encode("cp1252"), ', [Encoding=1252]').rows == [["é", "ü"]]
        assert self.csv(b"\xef\xbb\xbfa,b\n").rows == [["a", "b"]]
        assert self.csv(b"\xff\xfe" + "a,b\n".encode("utf-16-le"), ', [Encoding=TextEncoding.Utf16]').rows == [["a", "b"]]
        # A byte order mark wins over the encoding given (pivot-studio #104).
        assert self.csv(b"\xff\xfe" + "é,b\n".encode("utf-16-le"), ', [Encoding=65001]').rows == [["é", "b"]]
        assert self.csv(b"\xef\xbb\xbf" + "é,b\n".encode("utf-8"), ', [Encoding=1252]').rows == [["é", "b"]]

    def test_rows_as_a_desktop_refresh_reads_them(self):
        # pivot-studio #104's probes: short rows padded with "", the first row's width without Columns, a blank line
        # a row of "", a final line break no row, CR, LF and CR LF all end a record.
        assert self.csv(b"a,b,c\r\n1,2,3\r\n4,5\r\n6\r\n", ', [Columns=3]').rows == [
            ["a", "b", "c"], ["1", "2", "3"], ["4", "5", ""], ["6", "", ""]]
        assert self.csv(b"a,b\r\n1,2,3\r\n4,5\r\n").rows == [["a", "b"], ["1", "2"], ["4", "5"]]
        assert self.csv(b"a,b\r\n1,2\r\n\r\n3,4\r\n\r\n").rows == [["a", "b"], ["1", "2"], ["", ""], ["3", "4"],
                                                                     ["", ""]]
        assert self.csv(b"a,b\r1,2\r3,4\r").rows == self.csv(b"a,b\n1,2\n3,4").rows == [["a", "b"], ["1", "2"],
                                                                                         ["3", "4"]]

    def test_quotes_as_a_desktop_refresh_reads_them(self):
        assert self.csv(b'a,b\r\n"x"y,1\r\n').rows == [["a", "b"], ["xy", "1"]]
        assert self.csv(b'a,b\r\n "x" ,1\r\n').rows == [["a", "b"], [' "x" ', "1"]]
        assert self.csv(b'a,b\r\n"x,1\r\n2,3\r\n', ', [Columns=2]').rows == [["a", "b"], ["x,1\r\n2,3\r\n", ""]]
        none = ', [Columns=3, QuoteStyle=QuoteStyle.None]'
        assert self.csv(b'a,b,c\r\n1,"two\r\nlines","say ""hi"""\r\n2,"x,y",\r\n', none).rows == [
            ["a", "b", "c"], ["1", "two", ""], ['lines"', 'say "hi"', ""], ["2", "x,y", ""]]
        assert self.csv(b'a,b\r\n1,"x\r\n2,y"z"\r\n', ', [Columns=2, QuoteStyle=QuoteStyle.None]').rows == [
            ["a", "b"], ["1", "x"], ["2", 'y"z"']]


class TestExcel:
    def workbook(self):
        openpyxl = pytest.importorskip("openpyxl")
        from openpyxl.worksheet.table import Table as XTable
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sales"
        ws.append(["Region", "Amount", "When", "Flag"])
        ws.append(["East", 10.5, dt.datetime(2024, 1, 2), True])
        ws.append(["West", 3, None, False])
        hidden = wb.create_sheet("Hidden")
        hidden["B2"] = "only"
        hidden.sheet_state = "hidden"
        t = wb.create_sheet("T")
        t.append(["Key", "Name"])
        t.append([1, "a"])
        t.append([2, "b"])
        t.add_table(XTable(displayName="Keys", ref="A1:B3"))
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_sheets_and_tables(self):
        files = files_of(w=self.workbook())
        nav = ev('Excel.Workbook(File.Contents("w"), null, true)', files)
        assert nav.columns == ["Name", "Data", "Item", "Kind", "Hidden"]
        assert [(r[2], r[3], r[4]) for r in nav.rows] == [("Sales", "Sheet", False), ("Hidden", "Sheet", True),
                                                          ("T", "Sheet", False), ("Keys", "Table", False)]
        sheet = ev('Excel.Workbook(File.Contents("w"), null, true){[Item="Sales",Kind="Sheet"]}[Data]', files)
        assert sheet.columns == ["Column1", "Column2", "Column3", "Column4"]
        # A date-formatted cell is its stored number, as a Desktop refresh reads it (pivot-studio #104).
        assert sheet.rows == [["Region", "Amount", "When", "Flag"], ["East", 10.5, 45293, True],
                              ["West", 3, None, False]]
        table = ev('Excel.Workbook(File.Contents("w"), null, true){[Item="Keys",Kind="Table"]}[Data]', files)
        assert table.columns == ["Key", "Name"] and table.rows == [[1, "a"], [2, "b"]]

    def test_a_sheet_reads_its_stored_dimension(self):
        sheet = ev('Excel.Workbook(File.Contents("w"), null, true){[Item="Hidden",Kind="Sheet"]}[Data]',
                   files_of(w=self.workbook()))
        assert sheet.columns == ["Column1"] and sheet.rows == [["only"]]      # B2:B2

    def test_the_query_desktop_writes(self):
        m = ('let Source = Excel.Workbook(File.Contents("w"), null, true), '
             'Sheet = Source{[Item="Sales",Kind="Sheet"]}[Data], '
             '#"Promoted Headers" = Table.PromoteHeaders(Sheet), '
             '#"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers", '
             '{{"Region", type text}, {"Amount", type number}, {"When", type datetime}, {"Flag", type logical}}) '
             'in #"Changed Type"')
        t = ev(m, files_of(w=self.workbook()))
        assert t.columns == ["Region", "Amount", "When", "Flag"]
        assert force_table(t, errors_as_null=False) == [["East", 10.5, dt.datetime(2024, 1, 2), True],
                                                        ["West", 3, None, False]]

    def test_use_headers_promotes_the_first_row(self):
        sheet = ev('Excel.Workbook(File.Contents("w"), true, true){[Item="Sales",Kind="Sheet"]}[Data]',
                   files_of(w=self.workbook()))
        assert sheet.columns == ["Region", "Amount", "When", "Flag"] and sheet.rows[0] == ["East", 10.5, 45293, True]

    def test_what_no_refresh_settles_is_refused(self):
        with pytest.raises(MError, match="delayTypes"):
            ev('Excel.Workbook(File.Contents("w"))', files_of(w=self.workbook()))


class TestParquet:
    def parquet(self, **columns):
        pa = pytest.importorskip("pyarrow")
        import pyarrow.parquet as pq
        buf = io.BytesIO()
        pq.write_table(pa.table(columns), buf)
        return buf.getvalue()

    def test_plain_types(self):
        pa = pytest.importorskip("pyarrow")
        data = self.parquet(i=pa.array([1, None], pa.int64()), f=pa.array([1.5, 2.0]), s=pa.array(["a", None]),
                            b=pa.array([True, False]), d=pa.array([dt.date(2024, 1, 2), None]),
                            t=pa.array([dt.datetime(2024, 1, 2, 3, 4), None], pa.timestamp("us")))
        t = ev('Parquet.Document(File.Contents("p"))', files_of(p=data))
        assert t.columns == ["i", "f", "s", "b", "d", "t"]
        assert [x.facet or x.kind for x in t.types] == ["Int64.Type", "number", "text", "logical", "date", "datetime"]
        assert t.rows == [[1, 1.5, "a", True, dt.date(2024, 1, 2), dt.datetime(2024, 1, 2, 3, 4)],
                          [None, 2.0, None, False, None, None]]

    def test_types_as_a_desktop_refresh_reads_them(self):
        # pivot-studio #104: a float32 widened, a zoned timestamp at UTC whatever its zone, time, binary, decimal.
        pa = pytest.importorskip("pyarrow")
        from decimal import Decimal
        stamp = dt.datetime(2015, 11, 20, 10, 59, tzinfo=dt.timezone.utc)
        data = self.parquet(f=pa.array([0.1], pa.float32()), z=pa.array([stamp], pa.timestamp("us", tz="Australia/Sydney")),
                            t=pa.array([dt.time(10, 59, 30)], pa.time64("us")), b=pa.array([b"\x00\x01"], pa.binary()),
                            d=pa.array([Decimal("12345678.91")], pa.decimal128(10, 2)))
        t = ev('Parquet.Document(File.Contents("p"))', files_of(p=data))
        assert [x.kind for x in t.types] == ["number", "datetimezone", "time", "binary", "number"]
        row = t.rows[0]
        assert row[0] == pytest.approx(0.10000000149011612, abs=0) and row[1] == stamp and row[1].utcoffset() == dt.timedelta(0)
        assert row[2:] == [dt.time(10, 59, 30), b"\x00\x01", 12345678.91]

    def test_other_types_are_refused(self):
        pa = pytest.importorskip("pyarrow")
        with pytest.raises(MError, match="not supported"):
            ev('Parquet.Document(File.Contents("p"))', files_of(p=self.parquet(l=pa.array([[1], [2]]))))


class TestRefreshFromFiles:
    """A table whose M reads a file refreshes through the caller's mapping; without one it keeps its data."""

    def model(self, m):
        from pbix_mcp import server
        from pbix_mcp.builder import PBIXBuilder
        b = PBIXBuilder("Files")
        b.add_table("Sales", [{"name": "Region", "data_type": "String"}, {"name": "Amount", "data_type": "Int64"}],
                    rows=[{"Region": "East", "Amount": 1}])
        b.add_measure("Sales", "Total", "SUM('Sales'[Amount])")
        path = os.path.join(tempfile.mkdtemp(), "files.pbix")
        with open(path, "wb") as f:
            f.write(b.build())
        alias = f"files-{uuid.uuid4().hex[:12]}"
        assert json.loads(server.pbix_open(path, alias))["success"]
        assert json.loads(server.pbix_set_partition_m(alias, "Sales", m))["success"]
        return alias

    def test_mapped_and_unmapped(self):
        from pbix_mcp import server
        from pbix_mcp.refresh import refresh_tables
        m = ('let Source = Csv.Document(File.Contents("C:\\src\\sales.csv"), [Delimiter=",", Columns=2, '
             'Encoding=65001, QuoteStyle=QuoteStyle.Csv]), #"Promoted Headers" = Table.PromoteHeaders(Source), '
             '#"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers", {{"Region", type text}, '
             '{"Amount", Int64.Type}}) in #"Changed Type"')
        alias = self.model(m)
        try:
            result = refresh_tables(alias, ["Sales"])
            assert result.unmapped == {"Sales": "C:\\src\\sales.csv"} and result.tables == {}
            result = refresh_tables(alias, ["Sales"], files=files_of(**{"C:\\src\\sales.csv":
                                                                         b"Region,Amount\nEast,5\nWest,7\n"}))
            assert result.tables == {"Sales": 2} and not result.unmapped
            out = json.loads(server.pbix_evaluate_dax(alias, "Total"))
            assert out["results"][0]["value"] == 12
        finally:
            server.pbix_close(alias, force=True)


class TestReviewFindings:
    """Cases from an independent review of the first version."""

    def test_an_unmapped_file_is_never_absorbed(self):
        with pytest.raises(UnmappedSource):
            ev('try File.Contents("x") otherwise 5')
        with pytest.raises(UnmappedSource):
            ev('Table.AddColumn(#table({"p"}, {{"x"}}), "b", each File.Contents([p]))')

    def test_renames_that_overlap_are_refused(self):
        T = '#table({"a", "b"}, {{1, 2}})'
        for pairs in ('{{"a", "b"}, {"b", "a"}}', '{{"a", "b"}, {"b", "c"}}'):
            with pytest.raises(MError, match="more than one rename"):
                ev(f"Table.RenameColumns({T}, {pairs})")
        assert ev(f'Table.RenameColumns({T}, {{{{"a", "a"}}}})').columns == ["a", "b"]

    def test_csv_malformed_quoting_and_blank_lines(self):
        # As a Desktop refresh reads them (pivot-studio #104).
        read = lambda data: ev('Csv.Document(File.Contents("f"))', files_of(f=data)).rows  # noqa: E731
        assert read(b'a,"b\n') == [["a", "b\n"]]
        assert read(b'a,"b"c,d\n') == [["a", "bc", "d"]]
        assert read(b"a\n\nb\n") == [["a"], [""], ["b"]]
        assert read(b'a,b"c\r1,2\r') == [['a', 'b"c'], ["1", "2"]]

    def test_csv_options_record_with_positional_arguments_is_refused(self):
        with pytest.raises(MError, match="options record"):
            ev('Csv.Document(File.Contents("f"), [Columns=1], ";")', files_of(f=b"a\n"))

    def xlsx(self, build):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        build(wb)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_excel_honours_the_stored_dimension_for_rows_too(self):
        def build(wb):
            ws = wb.active
            ws.title = "S"
            ws["A1"], ws["C3"] = "x", 3
        data = self.xlsx(build)
        sheet = ev('Excel.Workbook(File.Contents("w"), null, true){[Item="S",Kind="Sheet"]}[Data]', files_of(w=data))
        assert sheet.rows == [["x", None, None], [None, None, None], [None, None, 3]]

    def test_excel_formulas_with_an_empty_cached_value_are_empty_text(self):
        def build(wb):
            ws = wb.active
            ws.title = "S"
            ws["A1"], ws["A2"] = 1, "=A1+1"      # openpyxl writes an empty cached value, <v />
        sheet = ev('Excel.Workbook(File.Contents("w"), null, true){[Item="S",Kind="Sheet"]}[Data]',
                   files_of(w=self.xlsx(build)))
        assert sheet.rows == [[1], [""]]            # as a Desktop refresh reads it (pivot-studio #104)

    def test_excel_error_values_are_failed_cells(self):
        def build(wb):
            ws = wb.active
            ws.title = "S"
            ws["A1"], ws["B1"] = "#DIV/0!", 1
        sheet = ev('Excel.Workbook(File.Contents("w"), null, true){[Item="S",Kind="Sheet"]}[Data]',
                   files_of(w=self.xlsx(build)))
        cell = sheet.rows[0][0]
        assert isinstance(cell, CellError) and cell.error.reason == "DataFormat.Error"
        assert cell.error.message == "Invalid cell value '#DIV/0!'." and sheet.rows[0][1] == 1

    def test_excel_trailing_empty_rows_of_the_dimension_are_dropped(self):
        def build(wb):
            ws = wb.active
            ws.title = "S"
            ws.append(["a", "b"])
            ws.append([1, 2])
        src = zipfile.ZipFile(io.BytesIO(self.xlsx(build)))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for info in src.infolist():
                body = src.read(info.filename)
                if info.filename == "xl/worksheets/sheet1.xml":
                    body = body.replace(b'<dimension ref="A1:B2"', b'<dimension ref="A1:D5"')
                z.writestr(info, body)
        files = files_of(w=out.getvalue())
        sheet = ev('Excel.Workbook(File.Contents("w"), null, true){[Item="S",Kind="Sheet"]}[Data]', files)
        assert sheet.rows == [["a", "b", None, None], [1, 2, None, None]]
        sheet = ev('Excel.Workbook(File.Contents("w"), [DelayTypes=true, InferSheetDimensions=true])'
                   '{[Item="S",Kind="Sheet"]}[Data]', files)
        assert sheet.rows == [["a", "b"], [1, 2]]

    def test_excel_a_table_with_a_totals_row_is_refused_and_a_table_named_like_a_sheet_is_kept_apart(self):
        from openpyxl.worksheet.table import Table as XTable

        def build(wb):
            ws = wb.active
            ws.title = "Keys"
            ws.append(["K"])
            ws.append([1])
            ws.add_table(XTable(displayName="Keys", ref="A1:A2"))
            t = wb.create_sheet("Tot")
            t.append(["V"])
            t.append([1])
            t.append([1])
            x = XTable(displayName="Tot1", ref="A1:A3")
            x.totalsRowCount = 1
            t.add_table(x)
        files = files_of(w=self.xlsx(build))
        nav = ev('Excel.Workbook(File.Contents("w"), null, true)', files)
        # Sheets first, then tables, as a Desktop refresh lists them (pivot-studio #104).
        assert [(r[2], r[3]) for r in nav.rows] == [("Keys", "Sheet"), ("Tot", "Sheet"), ("Keys", "Table"),
                                                    ("Tot1", "Table")]
        table = ev('Excel.Workbook(File.Contents("w"), null, true){[Item="Keys",Kind="Table"]}[Data]', files)
        assert table.columns == ["K"] and table.rows == [[1]]
        # A totals row is read as a row of the table.
        table = ev('Excel.Workbook(File.Contents("w"), null, true){[Item="Tot1",Kind="Table"]}[Data]', files)
        assert table.rows == [[1], [1]]

    def test_parquet_out_of_range_values_are_refused(self):
        pa = pytest.importorskip("pyarrow")
        import pyarrow.parquet as pq

        def parquet(**cols):
            buf = io.BytesIO()
            pq.write_table(pa.table(cols), buf)
            return buf.getvalue()
        assert ev('Parquet.Document(File.Contents("p"))',
                  files_of(p=parquet(u=pa.array([2 ** 64 - 1], pa.uint64())))).rows == [[2 ** 64 - 1]]
        from decimal import Decimal
        with pytest.raises(MError, match="more digits"):
            ev('Parquet.Document(File.Contents("p"))',
               files_of(p=parquet(d=pa.array([Decimal("1234567890123456789.123456")], pa.decimal128(28, 6)))))
        ns = pa.array([1_000_000_001], pa.timestamp("ns"))
        with pytest.raises(MError, match="can't be read"):
            ev('Parquet.Document(File.Contents("p"))', files_of(p=parquet(t=ns)))


class TestSecondReview:
    """An independent review of the Desktop rules (pivot-studio #104): a broken file is the file's error, never a
    Python exception, and what the review found unmeasured was measured on Desktop and follows it."""

    def book(self, patch=None, **sheets):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "S"
        ws.append(["a", 1])
        buf = io.BytesIO()
        wb.save(buf)
        if not patch:
            return buf.getvalue()
        src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for info in src.infolist():
                body = src.read(info.filename)
                z.writestr(info, patch(info.filename, body))
            for name, body in sheets.items():
                z.writestr(name, body)
        return out.getvalue()

    def sheet(self, data):
        return ev('Excel.Workbook(File.Contents("w"), null, true){[Item="S",Kind="Sheet"]}[Data]',
                  files_of(w=data)).rows

    def sheet_xml(self, cells, dimension="A1:A1"):
        def patch(name, body):
            if name != "xl/worksheets/sheet1.xml":
                return body
            head = body[:body.index(b"<sheetData>")]
            head = re.sub(rb'<dimension ref="[^"]*"', b'<dimension ref="' + dimension.encode() + b'"', head)
            return head + b"<sheetData>" + cells.encode() + b"</sheetData></worksheet>"
        return self.book(patch)

    def test_broken_packages_are_the_files_error(self):
        broken = [
            self.book(lambda n, b: b"<oops" if n == "xl/worksheets/sheet1.xml" else b),
            self.book(lambda n, b: b"<oops" if n == "xl/worksheets/_rels/sheet1.xml.rels" else b,
                      **{"xl/worksheets/_rels/sheet1.xml.rels": b""}),
            self.sheet_xml('<row r="1"><c r="A1" t="s"><v>0</v></c></row>'),           # no shared strings part
            self.sheet_xml('<row r="1"><c r="A1" t="s"><v>-1</v></c></row>'),
            self.sheet_xml('<row r="1"><c r="A1" t="b"><v>2</v></c></row>'),
            self.sheet_xml('<row r="1"><c r="A1"><v>-1E400</v></c></row>'),
            self.sheet_xml('<row r="1"><c r="A1"><v>1</v></c></row>', "A1:A99999999"),
            self.sheet_xml('<row r="1"><c r="A1"><v>1</v></c><c r="XFD1048576"/></row>', "A1:XFD1048576"),
            b"PK\x03\x04 not a zip",
        ]
        for data in broken:
            with pytest.raises(MError) as e:
                self.sheet(data)
            assert e.value.reason in ("DataFormat.Error", "Expression.Error")

    def test_escapes_and_defined_names(self):
        data = self.sheet_xml('<row r="1"><c r="A1" t="inlineStr"><is><t>x_x000D_y_x0009_z</t></is></c></row>')
        assert self.sheet(data) == [["x\ry\tz"]]

        def names(n, b):
            if n != "xl/workbook.xml":
                return b
            return b.replace(b"</sheets>", b'</sheets><definedNames><definedName name="Rng">S!$A$1</definedName>'
                                           b'<definedName name="_xlnm.Print_Area" localSheetId="0">S!$A$1</definedName>'
                                           b'</definedNames>')
        nav = ev('Excel.Workbook(File.Contents("w"), null, true)', files_of(w=self.book(names)))
        assert [(r[0], r[2], r[3], r[4]) for r in nav.rows] == [
            ("S", "S", "Sheet", False), ("Rng", "Rng", "DefinedName", False),
            ("_xlnm.Print_Area", "S!_xlnm.Print_Area", "DefinedName", False)]
        with pytest.raises(MError, match="defined name"):
            nav.rows[1][1].rows

    def test_option_types_are_checked(self):
        files = files_of(w=self.book())
        for m in ('Excel.Workbook(File.Contents("w"), 1, true)', 'Excel.Workbook(File.Contents("w"), null, "no")',
                  'Excel.Workbook(File.Contents("w"), [DelayTypes=true, InferSheetDimensions=1])'):
            with pytest.raises(MError):
                ev(m, files)
        for options in ('[QuoteStyle=true]', '[IncludeByteOrderMark=true]', '{"x", "x"}'):
            with pytest.raises(MError):
                ev(f'Csv.Document(File.Contents("f"), {options})', files_of(f=b"a,b\n"))
        with pytest.raises(MError, match="UTF-32"):
            ev('Csv.Document(File.Contents("f"))', files_of(f="a,b\n".encode("utf-32")))

    def test_promote_and_before_delimiter(self):
        assert ev('Table.PromoteHeaders(#table(3, {{"a", "a_1", "a"}, {1, 2, 3}}))').columns == ["a", "a_1", "a_2"]
        assert ev('Text.BeforeDelimiter("a-b", "-", {3, RelativePosition.FromEnd})') == ""
