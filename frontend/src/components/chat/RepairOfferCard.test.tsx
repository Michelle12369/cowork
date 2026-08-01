import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect } from 'vitest';
import RepairOfferCard from './RepairOfferCard';

const ONE_ERROR = [{ message: 'ReferenceError: foo is not defined', line: 1, col: 0 }];
const TWO_ERRORS = [
  { message: 'ReferenceError: foo is not defined', line: 1, col: 0 },
  { message: 'TypeError: Cannot read properties of null', line: 5, col: 3 },
];

describe('RepairOfferCard — pending state', () => {
  it('displays the error count and first error message', () => {
    render(
      <RepairOfferCard
        errorCount={2}
        firstErrorMessage={TWO_ERRORS[0].message}
        status="pending"
        onConfirm={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    expect(screen.getByText(/偵測到儀表板執行錯誤（2 個）/)).toBeInTheDocument();
    expect(screen.getByText('ReferenceError: foo is not defined')).toBeInTheDocument();
  });

  it('shows both 修復 and 忽略 buttons', () => {
    render(
      <RepairOfferCard
        errorCount={1}
        firstErrorMessage={ONE_ERROR[0].message}
        status="pending"
        onConfirm={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '修復' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '忽略' })).toBeInTheDocument();
  });

  it('clicking 修復 calls onConfirm', async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();

    render(
      <RepairOfferCard
        errorCount={1}
        firstErrorMessage={ONE_ERROR[0].message}
        status="pending"
        onConfirm={onConfirm}
        onDismiss={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: '修復' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('clicking 忽略 calls onDismiss', async () => {
    const onDismiss = vi.fn();
    const user = userEvent.setup();

    render(
      <RepairOfferCard
        errorCount={1}
        firstErrorMessage={ONE_ERROR[0].message}
        status="pending"
        onConfirm={vi.fn()}
        onDismiss={onDismiss}
      />,
    );

    await user.click(screen.getByRole('button', { name: '忽略' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('truncates firstErrorMessage longer than 120 characters', () => {
    const longMessage = 'A'.repeat(130);
    render(
      <RepairOfferCard
        errorCount={1}
        firstErrorMessage={longMessage}
        status="pending"
        onConfirm={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    // Should be truncated to 120 chars + ellipsis, not the full 130-char string
    expect(screen.queryByText(longMessage)).toBeNull();
    expect(screen.getByText(`${'A'.repeat(120)}…`)).toBeInTheDocument();
  });
});

describe('RepairOfferCard — repairing state', () => {
  it('shows spinner text and no action buttons', () => {
    render(
      <RepairOfferCard
        errorCount={1}
        firstErrorMessage={ONE_ERROR[0].message}
        status="repairing"
        onConfirm={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    expect(screen.getByText('修復中，請稍候…')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '修復' })).toBeNull();
    expect(screen.queryByRole('button', { name: '忽略' })).toBeNull();
  });
});

describe('RepairOfferCard — failed state', () => {
  it('shows 修復未成功 message with 再試一次 and 忽略 buttons', () => {
    render(
      <RepairOfferCard
        errorCount={1}
        firstErrorMessage={ONE_ERROR[0].message}
        status="failed"
        onConfirm={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    expect(screen.getByText('修復未成功')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '再試一次' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '忽略' })).toBeInTheDocument();
  });

  it('clicking 再試一次 calls onConfirm', async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();

    render(
      <RepairOfferCard
        errorCount={1}
        firstErrorMessage={ONE_ERROR[0].message}
        status="failed"
        onConfirm={onConfirm}
        onDismiss={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: '再試一次' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('clicking 忽略 in failed state calls onDismiss', async () => {
    const onDismiss = vi.fn();
    const user = userEvent.setup();

    render(
      <RepairOfferCard
        errorCount={1}
        firstErrorMessage={ONE_ERROR[0].message}
        status="failed"
        onConfirm={vi.fn()}
        onDismiss={onDismiss}
      />,
    );

    await user.click(screen.getByRole('button', { name: '忽略' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
