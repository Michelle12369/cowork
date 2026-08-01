import { splitAnswerByTableMarkers, extractReferencedTableIds } from './tableMarkers';
import type { TableResult } from '@/types';

const TABLE_1: TableResult = {
  tableId: 'tbl_1',
  intent: 'intent one',
  columns: ['a'],
  rows: [[1]],
  truncated: false,
};

test('no marker returns a single text segment with the full text', () => {
  const segments = splitAnswerByTableMarkers('plain answer', [TABLE_1]);
  expect(segments).toEqual([{ type: 'text', content: 'plain answer' }]);
});

test('a matched marker becomes a table segment between surrounding text segments', () => {
  const segments = splitAnswerByTableMarkers('before\n[[table:tbl_1]]\nafter', [TABLE_1]);
  expect(segments).toEqual([
    { type: 'text', content: 'before\n' },
    { type: 'table', table: TABLE_1 },
    { type: 'text', content: '\nafter' },
  ]);
});

test('an unmatched (unknown id) marker is dropped, not replaced with an empty table segment', () => {
  const segments = splitAnswerByTableMarkers('before\n[[table:tbl_unknown]]\nafter', [TABLE_1]);
  expect(segments).toEqual([
    { type: 'text', content: 'before\n' },
    { type: 'text', content: '\nafter' },
  ]);
});

test('a marker is dropped when no tables are supplied at all (history bubbles, decision 5)', () => {
  const segments = splitAnswerByTableMarkers('before\n[[table:tbl_1]]\nafter', undefined);
  expect(segments).toEqual([
    { type: 'text', content: 'before\n' },
    { type: 'text', content: '\nafter' },
  ]);
});

test('empty text returns no segments', () => {
  expect(splitAnswerByTableMarkers('', [TABLE_1])).toEqual([]);
});

test('extractReferencedTableIds collects every marker id in the text', () => {
  const ids = extractReferencedTableIds('[[table:tbl_1]] and [[table:tbl_2]]');
  expect(ids).toEqual(new Set(['tbl_1', 'tbl_2']));
});

test('extractReferencedTableIds returns an empty set when there is no marker', () => {
  expect(extractReferencedTableIds('no markers here')).toEqual(new Set());
});
