import { render, screen, fireEvent, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import PromptSender from './PromptSender';

test('Enter submits the trimmed value and clears the textarea', () => {
  const onSend = vi.fn();
  render(<PromptSender onSend={onSend} />);
  const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);

  fireEvent.change(textarea, { target: { value: '  hello  ' } });
  fireEvent.keyDown(textarea, { key: 'Enter' });

  expect(onSend).toHaveBeenCalledWith('hello');
  expect((textarea as HTMLTextAreaElement).value).toBe('');
});

test('Shift+Enter does not submit', () => {
  const onSend = vi.fn();
  render(<PromptSender onSend={onSend} />);
  const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);

  fireEvent.change(textarea, { target: { value: 'multi' } });
  fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });

  expect(onSend).not.toHaveBeenCalled();
});

test('Enter while composing (IME) does not submit', () => {
  const onSend = vi.fn();
  render(<PromptSender onSend={onSend} />);
  const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);

  fireEvent.change(textarea, { target: { value: '你好' } });
  fireEvent.compositionStart(textarea);
  // Enter pressed to commit the IME candidate — must NOT trigger onSend
  fireEvent.keyDown(textarea, { key: 'Enter' });
  expect(onSend).not.toHaveBeenCalled();

  // After composition ends, Enter submits normally
  fireEvent.compositionEnd(textarea);
  fireEvent.keyDown(textarea, { key: 'Enter' });
  expect(onSend).toHaveBeenCalledWith('你好');
});

test('prefill prop sets textarea value and calls onPrefillConsumed', async () => {
  const onPrefillConsumed = vi.fn();
  const { rerender } = render(
    <PromptSender onSend={vi.fn()} onPrefillConsumed={onPrefillConsumed} />,
  );

  await act(async () => {
    rerender(
      <PromptSender
        onSend={vi.fn()}
        prefill="Run an SPC analysis on Vt (gate CD)."
        onPrefillConsumed={onPrefillConsumed}
      />,
    );
  });

  const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);
  expect((textarea as HTMLTextAreaElement).value).toBe('Run an SPC analysis on Vt (gate CD).');
  expect(onPrefillConsumed).toHaveBeenCalledTimes(1);
});

// ── Stop button ───────────────────────────────────────────────────────────────

test('shows send button by default (isStreaming=false)', () => {
  render(<PromptSender onSend={vi.fn()} isStreaming={false} onStop={vi.fn()} />);
  expect(screen.queryByLabelText('Stop generation')).toBeNull();
  // The ArrowUp send button should be present (antd renders it with aria-label from icon)
  const buttons = screen.getAllByRole('button');
  // At least the send button and attach button exist
  expect(buttons.length).toBeGreaterThanOrEqual(1);
});

test('shows stop button when isStreaming=true and onStop provided', () => {
  render(<PromptSender onSend={vi.fn()} isStreaming={true} onStop={vi.fn()} />);
  expect(screen.getByLabelText('Stop generation')).toBeInTheDocument();
});

test('clicking the stop button calls onStop', async () => {
  const onStop = vi.fn();
  render(<PromptSender onSend={vi.fn()} isStreaming={true} onStop={onStop} />);
  await userEvent.click(screen.getByLabelText('Stop generation'));
  expect(onStop).toHaveBeenCalledTimes(1);
});

test('stop button is not shown when onStop is absent even if isStreaming=true', () => {
  render(<PromptSender onSend={vi.fn()} isStreaming={true} />);
  expect(screen.queryByLabelText('Stop generation')).toBeNull();
});

test('stop button is gray, not red (no danger class)', () => {
  render(<PromptSender onSend={vi.fn()} isStreaming={true} onStop={vi.fn()} />);
  const btn = screen.getByLabelText('Stop generation');
  expect(btn.className).not.toContain('ant-btn-dangerous');
  expect(btn.className).toContain('!bg-gray-500');
});

test('prefill value can be submitted via Enter and clears the textarea', async () => {
  const onSend = vi.fn();
  await act(async () => {
    render(
      <PromptSender
        onSend={onSend}
        prefill="Run an SPC analysis on Vt (gate CD)."
        onPrefillConsumed={vi.fn()}
      />,
    );
  });

  const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);
  expect((textarea as HTMLTextAreaElement).value).toBe('Run an SPC analysis on Vt (gate CD).');

  fireEvent.keyDown(textarea, { key: 'Enter' });
  expect(onSend).toHaveBeenCalledWith('Run an SPC analysis on Vt (gate CD).');
  expect((textarea as HTMLTextAreaElement).value).toBe('');
});
