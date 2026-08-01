import { render, screen, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import ChatHistorySidebar from './ChatHistorySidebar';

const sessions = [
  { id: 's1', title: 'SPC — Vt (gate CD)', updatedAt: new Date().toISOString() },
  {
    id: 's2',
    title: 'Defect pareto — W12',
    updatedAt: new Date(Date.now() - 3600e3).toISOString(),
  },
];

test('renders sessions and handles select + new chat', () => {
  const onSelect = vi.fn();
  const onNew = vi.fn();
  render(
    <ChatHistorySidebar
      sessions={sessions}
      activeId="s1"
      onSelect={onSelect}
      onNew={onNew}
      onCollapse={() => {}}
    />,
  );
  expect(screen.getByText('SPC — Vt (gate CD)')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Defect pareto — W12'));
  expect(onSelect).toHaveBeenCalledWith('s2');
  fireEvent.click(screen.getByText(/New chat/i));
  expect(onNew).toHaveBeenCalled();
});

test('collapse button calls onCollapse', () => {
  const onCollapse = vi.fn();
  render(
    <ChatHistorySidebar
      sessions={sessions}
      activeId={null}
      onSelect={() => {}}
      onNew={() => {}}
      onCollapse={onCollapse}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /Collapse chats/i }));
  expect(onCollapse).toHaveBeenCalled();
});
