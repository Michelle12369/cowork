import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import { App } from 'antd';
import QuestionCards from './QuestionCards';
import type { Question } from '@/types';

function renderWithApp(ui: React.ReactElement): ReturnType<typeof render> {
  return render(<App>{ui}</App>);
}

const singleQ: Question = {
  text: 'Which chart type?',
  options: ['Bar', 'Line', 'Pie'],
  multiSelect: false,
};

const multiQ: Question = {
  text: 'Select columns',
  options: ['col1', 'col2', 'col3'],
  multiSelect: true,
};

// ── Single-select ─────────────────────────────────────────────────────────────

test('single-select: renders question text and all options', () => {
  renderWithApp(<QuestionCards questions={[singleQ]} onAnswer={vi.fn()} />);
  expect(screen.getByText('Which chart type?')).toBeInTheDocument();
  expect(screen.getByText('Bar')).toBeInTheDocument();
  expect(screen.getByText('Line')).toBeInTheDocument();
  expect(screen.getByText('Pie')).toBeInTheDocument();
});

test('single-select: clicking an option calls onAnswer with the option text', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[singleQ]} onAnswer={onAnswer} />);

  await user.click(screen.getByText('Bar'));
  expect(onAnswer).toHaveBeenCalledWith('Bar');
  expect(onAnswer).toHaveBeenCalledTimes(1);
});

test('single-select: disabled buttons do not call onAnswer when clicked', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[singleQ]} onAnswer={onAnswer} disabled={true} />);

  await user.click(screen.getByText('Bar'));
  expect(onAnswer).not.toHaveBeenCalled();
});

// ── Multi-select ──────────────────────────────────────────────────────────────

test('multi-select: renders question text and options', () => {
  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={vi.fn()} />);
  expect(screen.getByText('Select columns')).toBeInTheDocument();
  expect(screen.getByText('col1')).toBeInTheDocument();
  expect(screen.getByText('送出')).toBeInTheDocument();
});

test('multi-select: 送出 button is disabled when nothing selected', () => {
  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={vi.fn()} />);
  const submitBtns = screen.getAllByRole('button', { name: '送出' });
  expect(submitBtns[0]).toBeDisabled();
});

test('multi-select: clicking an option does NOT call onAnswer immediately (no immediate-submit)', async () => {
  // Pins the routing condition's `&& !multiSelect` guard so a single multiSelect question
  // never falls into immediate-submit mode.
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={onAnswer} />);

  await user.click(screen.getByText('col1'));
  expect(onAnswer).not.toHaveBeenCalled();

  await user.click(screen.getByText('col2'));
  expect(onAnswer).not.toHaveBeenCalled();
});

test('multi-select: 送出 becomes enabled after selecting at least one option', async () => {
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={vi.fn()} />);

  const submitBtns = screen.getAllByRole('button', { name: '送出' });
  expect(submitBtns[0]).toBeDisabled();

  await user.click(screen.getByText('col1'));
  expect(submitBtns[0]).toBeEnabled();
});

test('multi-select: clicking 送出 calls onAnswer with selections joined by "、"', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={onAnswer} />);

  await user.click(screen.getByText('col1'));
  await user.click(screen.getByText('col2'));
  const submitBtns = screen.getAllByRole('button', { name: '送出' });
  await user.click(submitBtns[0]);

  expect(onAnswer).toHaveBeenCalledWith('col1、col2');
  expect(onAnswer).toHaveBeenCalledTimes(1);
});

test('multi-select: disabled state prevents submit', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={onAnswer} disabled={true} />);

  // toggling is blocked so selected stays empty; but also submit is disabled
  const submitBtns = screen.getAllByRole('button', { name: '送出' });
  await user.click(submitBtns[0]);
  expect(onAnswer).not.toHaveBeenCalled();
});

// ── Mixed questions ────────────────────────────────────────────────────────────

test('renders both single and multi-select questions', () => {
  renderWithApp(<QuestionCards questions={[singleQ, multiQ]} onAnswer={vi.fn()} />);
  expect(screen.getByText('Which chart type?')).toBeInTheDocument();
  expect(screen.getByText('Select columns')).toBeInTheDocument();
  expect(screen.getByText('送出')).toBeInTheDocument();
});

// ── Multiple questions: combined submit (no premature send) ──────────────────

test('multiple questions: clicking a single-select option does NOT submit immediately', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[singleQ, multiQ]} onAnswer={onAnswer} />);

  await user.click(screen.getByText('Bar'));
  // Selection is recorded (radio checked) but nothing is sent yet
  expect(onAnswer).not.toHaveBeenCalled();
  expect(screen.getByRole('radio', { name: 'Bar' })).toHaveAttribute('aria-checked', 'true');
});

test('multiple questions: 送出 stays disabled until every question is answered', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[singleQ, multiQ]} onAnswer={onAnswer} />);

  const submitBtn = screen.getByRole('button', { name: '送出' });
  expect(submitBtn).toBeDisabled();

  // Answer only the first question — still disabled
  await user.click(screen.getByText('Bar'));
  expect(submitBtn).toBeDisabled();

  // Answer the second question — now enabled
  await user.click(screen.getByText('col1'));
  expect(submitBtn).toBeEnabled();
});

test('multiple questions: submit combines every answer prefixed with its question text', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[singleQ, multiQ]} onAnswer={onAnswer} />);

  await user.click(screen.getByText('Bar'));
  await user.click(screen.getByText('col3'));
  await user.click(screen.getByText('col1'));
  await user.click(screen.getByRole('button', { name: '送出' }));

  expect(onAnswer).toHaveBeenCalledWith('Which chart type?：Bar\nSelect columns：col1、col3');
  expect(onAnswer).toHaveBeenCalledTimes(1);
});

test('multiple questions: single-select behaves like radio — new click replaces selection', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[singleQ, multiQ]} onAnswer={onAnswer} />);

  await user.click(screen.getByText('Bar'));
  await user.click(screen.getByText('Line'));
  expect(screen.getByRole('radio', { name: 'Bar' })).toHaveAttribute('aria-checked', 'false');
  expect(screen.getByRole('radio', { name: 'Line' })).toHaveAttribute('aria-checked', 'true');

  await user.click(screen.getByText('col2'));
  await user.click(screen.getByRole('button', { name: '送出' }));
  expect(onAnswer).toHaveBeenCalledWith('Which chart type?：Line\nSelect columns：col2');
});

// ── Multi-select answer order ──────────────────────────────────────────────────

test('multi-select: answer joins by display order even when options are clicked in reverse', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();

  // options: ['col1', 'col2', 'col3'] — click in reverse: col3 → col1 → col2
  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={onAnswer} />);

  await user.click(screen.getByText('col3'));
  await user.click(screen.getByText('col1'));
  await user.click(screen.getByText('col2'));

  const submitBtns = screen.getAllByRole('button', { name: '送出' });
  await user.click(submitBtns[0]);

  // Must join in display order (col1、col2、col3), not click order (col3、col1、col2)
  expect(onAnswer).toHaveBeenCalledWith('col1、col2、col3');
});

// ── "其他" option ─────────────────────────────────────────────────────────────

test('single-select: renders an "其他" button for each question', () => {
  renderWithApp(<QuestionCards questions={[singleQ]} onAnswer={vi.fn()} />);
  // immediate mode — button role
  expect(screen.getByRole('button', { name: /其他/ })).toBeInTheDocument();
});

test('single-select: clicking "其他" expands the free-text input', async () => {
  const user = userEvent.setup();
  renderWithApp(<QuestionCards questions={[singleQ]} onAnswer={vi.fn()} />);
  expect(screen.queryByPlaceholderText('請輸入...')).toBeNull();
  await user.click(screen.getByRole('button', { name: /其他/ }));
  expect(screen.getByPlaceholderText('請輸入...')).toBeInTheDocument();
});

test('single-select: entering text and pressing Enter in "其他" input calls onAnswer', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();
  renderWithApp(<QuestionCards questions={[singleQ]} onAnswer={onAnswer} />);
  await user.click(screen.getByRole('button', { name: /其他/ }));
  const input = screen.getByPlaceholderText('請輸入...');
  await user.type(input, 'custom option');
  await user.keyboard('{Enter}');
  expect(onAnswer).toHaveBeenCalledWith('custom option');
  expect(onAnswer).toHaveBeenCalledTimes(1);
});

test('single-select: clicking the 送出 button inside "其他" input calls onAnswer', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();
  renderWithApp(<QuestionCards questions={[singleQ]} onAnswer={onAnswer} />);
  await user.click(screen.getByRole('button', { name: /其他/ }));
  await user.type(screen.getByPlaceholderText('請輸入...'), 'my answer');
  // The inline 送出 button is inside the expanded area
  const submitBtns = screen.getAllByRole('button', { name: '送出' });
  await user.click(submitBtns[submitBtns.length - 1]);
  expect(onAnswer).toHaveBeenCalledWith('my answer');
});

test('single-select: empty "其他" input cannot be submitted', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();
  renderWithApp(<QuestionCards questions={[singleQ]} onAnswer={onAnswer} />);
  await user.click(screen.getByRole('button', { name: /其他/ }));
  // Press Enter without typing anything
  await user.keyboard('{Enter}');
  expect(onAnswer).not.toHaveBeenCalled();
  // The inline 送出 button should be disabled
  const submitBtns = screen.getAllByRole('button', { name: '送出' });
  expect(submitBtns[submitBtns.length - 1]).toBeDisabled();
});

test('multi-select: "其他" is a checkbox that can be toggled', async () => {
  const user = userEvent.setup();
  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={vi.fn()} />);
  const otherBtn = screen.getByRole('checkbox', { name: /其他/ });
  expect(otherBtn).toHaveAttribute('aria-checked', 'false');
  await user.click(otherBtn);
  expect(otherBtn).toHaveAttribute('aria-checked', 'true');
});

test('multi-select: toggling "其他" expands free-text input', async () => {
  const user = userEvent.setup();
  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={vi.fn()} />);
  expect(screen.queryByPlaceholderText('請輸入...')).toBeNull();
  await user.click(screen.getByRole('checkbox', { name: /其他/ }));
  expect(screen.getByPlaceholderText('請輸入...')).toBeInTheDocument();
});

test('multi-select: submit with "其他" text merges it into the answer', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();
  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={onAnswer} />);
  await user.click(screen.getByText('col1'));
  await user.click(screen.getByRole('checkbox', { name: /其他/ }));
  await user.type(screen.getByPlaceholderText('請輸入...'), 'custom col');
  const submitBtns = screen.getAllByRole('button', { name: '送出' });
  await user.click(submitBtns[0]);
  expect(onAnswer).toHaveBeenCalledWith('col1、custom col');
});

test('multi-select: "其他" selected but empty text blocks submit', async () => {
  const onAnswer = vi.fn();
  const user = userEvent.setup();
  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={onAnswer} />);
  // Toggle 其他 (no text entered)
  await user.click(screen.getByRole('checkbox', { name: /其他/ }));
  const submitBtns = screen.getAllByRole('button', { name: '送出' });
  expect(submitBtns[0]).toBeDisabled();
  await user.click(submitBtns[0]);
  expect(onAnswer).not.toHaveBeenCalled();
});

// ── Multi-select ARIA ─────────────────────────────────────────────────────────

test('multi-select: toggle buttons have role="checkbox" and correct aria-checked', async () => {
  const user = userEvent.setup();

  renderWithApp(<QuestionCards questions={[multiQ]} onAnswer={vi.fn()} />);

  const col1Btn = screen.getByRole('checkbox', { name: 'col1' });
  const col2Btn = screen.getByRole('checkbox', { name: 'col2' });

  // Initially unchecked
  expect(col1Btn).toHaveAttribute('aria-checked', 'false');
  expect(col2Btn).toHaveAttribute('aria-checked', 'false');

  // After clicking col1 → checked
  await user.click(col1Btn);
  expect(col1Btn).toHaveAttribute('aria-checked', 'true');
  expect(col2Btn).toHaveAttribute('aria-checked', 'false');

  // Clicking again → unchecked
  await user.click(col1Btn);
  expect(col1Btn).toHaveAttribute('aria-checked', 'false');
});
