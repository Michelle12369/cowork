import { render, screen } from '@testing-library/react';
import { vi, beforeAll, afterAll } from 'vitest';
import ErrorBoundary from './ErrorBoundary';

const ThrowingChild = (): never => {
  throw new Error('Test explosion');
};

beforeAll(() => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterAll(() => {
  vi.mocked(console.error).mockRestore();
});

test('renders error fallback when child throws', () => {
  render(
    <ErrorBoundary>
      <ThrowingChild />
    </ErrorBoundary>,
  );
  expect(screen.getByText('Failed to load')).toBeInTheDocument();
  expect(screen.getByText('Test explosion')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Reload/i })).toBeInTheDocument();
});

test('renders a custom fallback instead of the default panel when provided', () => {
  const { container } = render(
    <ErrorBoundary fallback={null}>
      <ThrowingChild />
    </ErrorBoundary>,
  );
  expect(screen.queryByText('Failed to load')).not.toBeInTheDocument();
  expect(container).toBeEmptyDOMElement();
});
