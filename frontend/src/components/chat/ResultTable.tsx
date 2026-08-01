import React, { useMemo } from 'react';
import { Table } from 'antd';
import type { TableColumnsType } from 'antd';
import type { TableCellValue } from '@/types';

export interface Props {
  intent: string;
  columns: string[];
  rows: TableCellValue[][];
  truncated: boolean;
}

/** Number of rows above which antd's default pagination kicks in. */
const PAGINATION_THRESHOLD = 20;
const PAGE_SIZE = 20;

type ResultTableRecord = Record<string, TableCellValue> & { key: string };

/** Trims float noise via significant digits (toPrecision(12)), not a fixed decimal position —
 *  a fixed position would corrupt large-magnitude values. Re-expands JS's exponent fallback. */
function expandExponentialNotation(exponentialText: string): string {
  const exponentMatch = /^(-?)(\d+)(?:\.(\d+))?e([+-]\d+)$/i.exec(exponentialText);
  if (!exponentMatch) return exponentialText;
  const [, sign, integerDigits, fractionDigits = '', exponentText] = exponentMatch;
  const digits = integerDigits + fractionDigits;
  const decimalPointPosition = integerDigits.length + Number.parseInt(exponentText, 10);
  if (decimalPointPosition <= 0) {
    return `${sign}0.${'0'.repeat(-decimalPointPosition)}${digits}`;
  }
  if (decimalPointPosition >= digits.length) {
    return `${sign}${digits}${'0'.repeat(decimalPointPosition - digits.length)}`;
  }
  return `${sign}${digits.slice(0, decimalPointPosition)}.${digits.slice(decimalPointPosition)}`;
}

function formatCellValue(value: TableCellValue): string {
  if (value === null) return '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || Number.isInteger(value)) return String(value);
    // toPrecision(12) then re-parsing as a Number drops trailing zeros exactly like the
    // backend's shorten-then-rstrip("0") does, without a regex.
    const shortened = Number(value.toPrecision(12)).toString();
    return shortened.includes('e') ? expandExponentialNotation(shortened) : shortened;
  }
  return String(value);
}

/** Renders one TABLE SSE event: an intent caption above an antd Table of the query result. */
const ResultTable: React.FC<Props> = ({
  intent,
  columns: rawColumns,
  rows: rawRows,
  truncated,
}) => {
  // Defensive: the wire contract guarantees both fields, but a contract violation
  // (e.g. Jackson nulling a missing field upstream) must not crash the live bubble.
  const columns = rawColumns ?? [];
  const rows = rawRows ?? [];
  const tableColumns: TableColumnsType<ResultTableRecord> = useMemo(
    () =>
      columns.map((columnName, columnIndex) => ({
        title: columnName,
        dataIndex: `col_${columnIndex}`,
        key: `col_${columnIndex}`,
        render: (value: TableCellValue) => formatCellValue(value),
      })),
    [columns],
  );

  const dataSource: ResultTableRecord[] = useMemo(
    () =>
      rows.map((row, rowIndex) => {
        const record: ResultTableRecord = { key: `row-${rowIndex}` };
        row.forEach((cellValue, columnIndex) => {
          record[`col_${columnIndex}`] = cellValue;
        });
        return record;
      }),
    [rows],
  );

  return (
    <div className="mt-2 rounded-lg border border-gray-200 bg-white p-3">
      <div className="mb-2 text-xs text-gray-500">{intent}</div>
      <Table<ResultTableRecord>
        columns={tableColumns}
        dataSource={dataSource}
        size="small"
        pagination={rows.length > PAGINATION_THRESHOLD ? { pageSize: PAGE_SIZE } : false}
        scroll={{ x: 'max-content' }}
      />
      {truncated && <div className="mt-1 text-[11px] text-gray-400">(前 200 列)</div>}
    </div>
  );
};

export default React.memo(ResultTable);
