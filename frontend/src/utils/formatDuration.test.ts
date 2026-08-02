import { formatDuration } from './formatDuration';

test('formats sub-minute durations as seconds', () => {
  expect(formatDuration(45_000)).toBe('45 s');
});

test('formats minute-plus durations as minutes and seconds', () => {
  expect(formatDuration(83_000)).toBe('1 min 23 s');
});

test('exact minutes keep the zero-second part', () => {
  expect(formatDuration(60_000)).toBe('1 min 0 s');
});

test('sub-second durations floor to 1 second', () => {
  expect(formatDuration(400)).toBe('1 s');
});
