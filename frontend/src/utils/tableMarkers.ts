import type { TableResult } from '@/types';

/** Matches `[[table:tbl_abc123]]` — same display-level marker precedent as the legacy
 *  `[[step:]]` convention: it drives rendering only, never control flow. */
const TABLE_MARKER_PATTERN = /\[\[table:([^\]]+)\]\]/g;

export interface AnswerTextSegment {
  type: 'text';
  content: string;
}

export interface AnswerTableSegment {
  type: 'table';
  table: TableResult;
}

export type AnswerSegment = AnswerTextSegment | AnswerTableSegment;

/**
 * Splits answer text on `[[table:<tableId>]]` markers, resolving each id against the
 * accumulated TABLE events. A marker with no match is dropped silently — the raw
 * `[[table:...]]` text must never reach the user.
 */
export function splitAnswerByTableMarkers(
  text: string,
  tables: TableResult[] | undefined,
): AnswerSegment[] {
  const tablesById = new Map((tables ?? []).map((table) => [table.tableId, table]));
  const segments: AnswerSegment[] = [];
  let cursor = 0;

  for (const match of text.matchAll(TABLE_MARKER_PATTERN)) {
    const matchIndex = match.index ?? 0;
    const textBefore = text.slice(cursor, matchIndex);
    if (textBefore) segments.push({ type: 'text', content: textBefore });

    const table = tablesById.get(match[1]);
    if (table) segments.push({ type: 'table', table });

    cursor = matchIndex + match[0].length;
  }

  const trailingText = text.slice(cursor);
  if (trailingText) segments.push({ type: 'text', content: trailingText });

  return segments;
}

/** Ids referenced by a `[[table:id]]` marker in the answer — used to hide those tables from
 *  the collapsed intermediate-tables list, since they're shown inline in the answer instead. */
export function extractReferencedTableIds(text: string): Set<string> {
  const ids = new Set<string>();
  for (const match of text.matchAll(TABLE_MARKER_PATTERN)) {
    ids.add(match[1]);
  }
  return ids;
}
