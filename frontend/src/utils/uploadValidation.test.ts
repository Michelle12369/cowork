import { validateFiles } from './uploadValidation';
import type { AppConfig } from '@/api/configApi';
import type { UploadedFileInfo } from '@/types';

const GB = 1024 ** 3;
const MB = 1024 ** 2;

const TEST_LIMITS: AppConfig = {
  retentionDays: 30,
  maxFiles: 5,
  maxSessionBytes: 5 * GB,
  singleFileLimits: {
    csv: 2 * GB,
    xlsx: 200 * MB,
  },
};

// antd/jsdom File has no writable size — construct then override
function makeFile(name: string, size: number): File {
  const file = new File([], name);
  Object.defineProperty(file, 'size', { value: size, configurable: true });
  return file;
}

function makeExisting(count: number, expired = false): UploadedFileInfo[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `f${index}`,
    name: `file${index}.csv`,
    alias: `file${index}`,
    sizeBytes: 1_000,
    type: 'csv',
    rowCount: null,
    expired,
  }));
}

test('returns error when total file count exceeds 5', () => {
  const existing = makeExisting(4); // 4 active
  const incoming = [makeFile('a.csv', 1_000), makeFile('b.csv', 1_000)]; // 4 + 2 = 6
  expect(validateFiles(existing, incoming, TEST_LIMITS)).toBe(
    'You can attach up to 5 files per chat.',
  );
});

test('returns error for unsupported file extension', () => {
  expect(validateFiles([], [makeFile('photo.png', 1_000)], TEST_LIMITS)).toBe(
    'Unsupported file type: .png',
  );
});

test('returns error when single csv exceeds 2 GB', () => {
  const big = makeFile('huge.csv', 2 * GB + 1);
  expect(validateFiles([], [big], TEST_LIMITS)).toBe('huge.csv exceeds the size limit.');
});

test('returns error when total size would exceed 5 GB', () => {
  const existing: UploadedFileInfo[] = [
    {
      id: 'e1',
      name: 'big.csv',
      alias: 'big',
      sizeBytes: 4 * GB,
      type: 'csv',
      rowCount: null,
      expired: false,
    },
  ];
  const incoming = [makeFile('extra.csv', 1.5 * GB)]; // 4 + 1.5 = 5.5 GB > 5 GB
  expect(validateFiles(existing, incoming, TEST_LIMITS)).toBe(
    'Total attachments would exceed 5.0 GB.',
  );
});

test('returns null for valid files', () => {
  const existing = makeExisting(1);
  expect(validateFiles(existing, [makeFile('data.csv', 1_000)], TEST_LIMITS)).toBeNull();
});

test('expired existing files do not count toward the file limit', () => {
  const existing = makeExisting(4, true); // 4 expired — excluded
  const incoming = Array.from({ length: 5 }, (_, index) => makeFile(`f${index}.csv`, 1_000)); // 0 + 5 = 5 ≤ 5
  expect(validateFiles(existing, incoming, TEST_LIMITS)).toBeNull();
});

test('uses limits.maxFiles from config for the file count threshold', () => {
  const strictLimits: AppConfig = { ...TEST_LIMITS, maxFiles: 2 };
  const existing = makeExisting(2); // 2 active
  const incoming = [makeFile('extra.csv', 1_000)]; // 2 + 1 = 3 > 2
  expect(validateFiles(existing, incoming, strictLimits)).toBe(
    'You can attach up to 2 files per chat.',
  );
});
