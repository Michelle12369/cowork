import { render, screen, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import QuickChips from './QuickChips';
import { QUICK_PROMPTS } from '@/config/quickPrompts';

test('renders all configured chips and emits prompt on click', () => {
  const onPick = vi.fn();
  render(<QuickChips onPick={onPick} />);
  for (const p of QUICK_PROMPTS) {
    expect(screen.getByText(p.label)).toBeInTheDocument();
  }
  fireEvent.click(screen.getByText(QUICK_PROMPTS[0].label));
  expect(onPick).toHaveBeenCalledWith(QUICK_PROMPTS[0].prompt);
});

test('disabled chips carry the native disabled attribute and do not trigger onPick', () => {
  const onPick = vi.fn();
  render(<QuickChips onPick={onPick} disabled />);
  const chip = screen.getByText(QUICK_PROMPTS[0].label);
  expect(chip).toBeDisabled();
  fireEvent.click(chip);
  expect(onPick).not.toHaveBeenCalled();
});
