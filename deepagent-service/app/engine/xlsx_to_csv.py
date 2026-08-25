"""xlsx→CSV 轉檔(openpyxl read-only streaming)。

語意對齊 Java 舊管線(POI DataFormatter):僅第一張 sheet、cell 輸出為文字。
已知差異(允收):數字格式代碼(千分位/百分比樣式)不重現——輸出原始值文字;
datetime 輸出 `YYYY-MM-DD HH:MM:SS`。壞檔(未解密密文/非 xlsx)由 openpyxl
直接 raise——fail loud 是設計要求,勿包成靜默略過。
"""

import csv
from datetime import datetime
from pathlib import Path

import openpyxl


def _format_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def convert_xlsx_to_csv(xlsx_path: Path, csv_path: Path) -> None:
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        first_sheet = workbook.worksheets[0]
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            for row in first_sheet.iter_rows(values_only=True):
                writer.writerow([_format_cell(cell) for cell in row])
    finally:
        workbook.close()
