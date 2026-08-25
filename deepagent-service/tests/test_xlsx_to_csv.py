from datetime import datetime
from pathlib import Path

import openpyxl

from app.engine.xlsx_to_csv import convert_xlsx_to_csv


def _write_xlsx(path: Path, rows: list[list[object]], extra_sheet: bool = False) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    if extra_sheet:
        second = workbook.create_sheet("second")
        second.append(["should", "not", "appear"])
    workbook.save(path)


def test_convert_first_sheet_values_to_csv(tmp_path: Path) -> None:
    source = tmp_path / "input.xlsx"
    _write_xlsx(source, [["name", "count"], ["widget", 3], ["gadget", 1.5]])
    output = tmp_path / "out.csv"
    convert_xlsx_to_csv(source, output)
    assert output.read_text(encoding="utf-8").splitlines() == [
        "name,count",
        "widget,3",
        "gadget,1.5",
    ]


def test_convert_only_first_sheet(tmp_path: Path) -> None:
    source = tmp_path / "multi.xlsx"
    _write_xlsx(source, [["a"]], extra_sheet=True)
    output = tmp_path / "out.csv"
    convert_xlsx_to_csv(source, output)
    assert "appear" not in output.read_text(encoding="utf-8")


def test_convert_none_and_datetime_cells(tmp_path: Path) -> None:
    source = tmp_path / "mixed.xlsx"
    _write_xlsx(source, [["when", "note"], [datetime(2026, 8, 26, 9, 30), None]])  # noqa: DTZ001
    output = tmp_path / "out.csv"
    convert_xlsx_to_csv(source, output)
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "2026-08-26 09:30:00,"  # datetime→isoformat(sep=' ')、None→空字串


def test_convert_invalid_xlsx_raises(tmp_path: Path) -> None:
    source = tmp_path / "garbage.xlsx"
    source.write_bytes(b"not a zip at all")
    import pytest

    with pytest.raises(Exception):  # noqa: B017 fail loud:internal 未解密的密文走到這裡必炸
        convert_xlsx_to_csv(source, tmp_path / "out.csv")
