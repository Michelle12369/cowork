/**
 * Thinking-toggle behaviour tests.
 *
 * ThinkingPanel was inlined into MessageBubble — the "Working on it…" row now
 * doubles as the collapsible toggle for reasoning content.  These tests verify
 * that contract through MessageBubble directly.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import MessageBubble from './MessageBubble';
import type { StepItem } from '@/types';

// Stub out siblings so this file stays focused on the thinking toggle.
vi.mock('./StepChain', () => ({
  default: ({ steps }: { steps: StepItem[] }) => (
    <div data-testid="step-chain">{steps.map((step) => step.title).join(',')}</div>
  ),
}));
vi.mock('./QuestionCards', () => ({ default: () => null }));

test('"Working on it…" header is visible when streaming with thinking', () => {
  render(<MessageBubble sender="AI" text="" thinking="some thought" streaming={true} />);
  expect(screen.getByText(/Working on it/i)).toBeInTheDocument();
});

test('"Working on it…" header is visible after streaming ends when thinking is present', () => {
  render(<MessageBubble sender="AI" text="" thinking="some thought" streaming={false} />);
  expect(screen.getByText(/Working on it/i)).toBeInTheDocument();
});

test('content is hidden by default (collapsed)', () => {
  render(<MessageBubble sender="AI" text="" thinking="reasoning content" streaming={true} />);
  expect(screen.queryByText('reasoning content')).not.toBeInTheDocument();
});

test('expands to show thinking content on button click', async () => {
  const user = userEvent.setup();
  render(<MessageBubble sender="AI" text="" thinking="reasoning content" streaming={true} />);

  await user.click(screen.getByRole('button', { name: /Working on it/i }));
  expect(screen.getByText('reasoning content')).toBeInTheDocument();
});

test('collapses again on second button click', async () => {
  const user = userEvent.setup();
  render(<MessageBubble sender="AI" text="" thinking="reasoning content" streaming={true} />);

  await user.click(screen.getByRole('button', { name: /Working on it/i }));
  expect(screen.getByText('reasoning content')).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: /Working on it/i }));
  expect(screen.queryByText('reasoning content')).not.toBeInTheDocument();
});

test('content area has monospace font style when expanded', async () => {
  const user = userEvent.setup();
  const { container } = render(
    <MessageBubble sender="AI" text="" thinking="code text" streaming={true} />,
  );

  await user.click(screen.getByRole('button', { name: /Working on it/i }));
  const contentDiv = container.querySelector('[style*="monospace"]');
  expect(contentDiv).not.toBeNull();
});
