/**
 * ConnectorPicker tests — graceful-empty catalog, locked read-only display, active-files
 * mutual exclusion, and selection wiring.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import ConnectorPicker from './ConnectorPicker';
import type { ConnectorInfo } from '@/types';

const CONNECTORS: ConnectorInfo[] = [
  { id: 'salesforce', name: 'Salesforce CRM' },
  { id: 'jira', name: 'Jira' },
];

/** Pre-seeds the ['connectors'] query so useSuspenseQuery resolves synchronously without
 *  a real network call or an actual Suspense boundary needed in the test tree. */
function renderWithConnectors(
  connectors: ConnectorInfo[],
  props: Partial<React.ComponentProps<typeof ConnectorPicker>> = {},
): { onChange: ReturnType<typeof vi.fn> } {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(['connectors'], connectors);
  const onChange = vi.fn();

  render(
    <QueryClientProvider client={queryClient}>
      <ConnectorPicker selectedIds={[]} onChange={onChange} {...props} />
    </QueryClientProvider>,
  );

  return { onChange };
}

describe('ConnectorPicker — empty catalog', () => {
  it('emptyCatalog_render_rendersNothing', () => {
    const { container } = render(
      <QueryClientProvider
        client={(() => {
          const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
          queryClient.setQueryData(['connectors'], []);
          return queryClient;
        })()}
      >
        <ConnectorPicker selectedIds={[]} onChange={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(container).toBeEmptyDOMElement();
  });
});

describe('ConnectorPicker — locked session', () => {
  it('lockedConnectorIds_nonEmpty_showsReadOnlyLockedText', () => {
    renderWithConnectors(CONNECTORS, { lockedConnectorIds: ['salesforce'] });

    expect(screen.getByText('資料源已鎖定——換資料源請開新對話')).toBeInTheDocument();
    // The interactive multi-select must not render while locked.
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });
});

describe('ConnectorPicker — active files mutual exclusion', () => {
  it('hasActiveFiles_true_disablesSelect', () => {
    renderWithConnectors(CONNECTORS, { hasActiveFiles: true });

    const combobox = screen.getByRole('combobox');
    expect(combobox).toBeDisabled();
  });

  it('hasActiveFiles_false_selectEnabled', () => {
    renderWithConnectors(CONNECTORS, { hasActiveFiles: false });

    const combobox = screen.getByRole('combobox');
    expect(combobox).not.toBeDisabled();
  });
});

describe('ConnectorPicker — selection', () => {
  it('selectOption_click_callsOnChangeWithSelectedId', async () => {
    const user = userEvent.setup();
    const { onChange } = renderWithConnectors(CONNECTORS);

    await user.click(screen.getByRole('combobox'));
    await user.click(await screen.findByText('Salesforce CRM'));

    expect(onChange).toHaveBeenCalledWith(['salesforce']);
  });
});
