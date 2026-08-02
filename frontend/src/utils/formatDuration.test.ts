import { formatDuration } from './formatDuration';

test('formats sub-minute durations as seconds', () => {
  expect(formatDuration(45_000)).toBe('45 秒');
});

test('formats minute-plus durations as minutes and seconds', () => {
  expect(formatDuration(83_000)).toBe('1 分 23 秒');
});

test('exact minutes keep the zero-second part', () => {
  expect(formatDuration(60_000)).toBe('1 分 0 秒');
});

test('sub-second durations floor to 1 second', () => {
  expect(formatDuration(400)).toBe('1 秒');
});
