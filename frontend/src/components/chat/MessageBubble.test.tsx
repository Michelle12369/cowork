import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import MessageBubble from './MessageBubble';
import type { Question, StepItem, TableResult } from '@/types';

// Mock artifactApi so tests can control fetch outcomes without network calls.
vi.mock('@/api/artifactApi', () => ({
  fetchArtifactRawHtml: vi.fn(),
}));

import { fetchArtifactRawHtml } from '@/api/artifactApi';

// Mock StepChain to avoid antd-x complexity; exposes the `tables` prop so tests can assert
// MessageBubble forwards the right (non-referenced) subset without depending on StepChain's own logic.
vi.mock('./StepChain', () => ({
  default: ({ steps, tables }: { steps: StepItem[]; tables?: TableResult[] }) => (
    <div
      data-testid="step-chain"
      data-table-ids={(tables ?? []).map((table) => table.tableId).join(',')}
    >
      {steps.map((step) => step.title).join(',')}
    </div>
  ),
}));

// Mock ResultTable to keep MessageBubble tests focused on wiring, not antd Table internals
vi.mock('./ResultTable', () => ({
  default: ({ intent, columns }: { intent: string; columns: string[] }) => (
    <div data-testid="result-table" data-columns={columns.join(',')}>
      {intent}
    </div>
  ),
}));

// Mock QuestionCards to keep MessageBubble tests focused
vi.mock('./QuestionCards', () => ({
  default: ({
    questions,
    disabled,
    onAnswer,
  }: {
    questions: Question[];
    disabled?: boolean;
    onAnswer: (t: string) => void;
  }) => (
    <div data-testid="question-cards" data-disabled={disabled}>
      {questions.map((question, questionIndex) => (
        <button key={questionIndex} onClick={() => onAnswer(question.options[0])}>
          {question.text}
        </button>
      ))}
    </div>
  ),
}));

test('USER bubble is right-aligned with blue background', () => {
  const { container } = render(<MessageBubble sender="USER" text="Hello" />);
  expect(screen.getByText('Hello')).toBeInTheDocument();
  expect(container.querySelector('[class*="justify-end"]')).not.toBeNull();
  expect(container.querySelector('[class*="bg-blue-"]')).not.toBeNull();
});

test('AI bubble shows eRD AI label', () => {
  render(<MessageBubble sender="AI" text="Here is the analysis" />);
  expect(screen.getByText(/eRD AI/i)).toBeInTheDocument();
  expect(screen.getByText('Here is the analysis')).toBeInTheDocument();
});

test('AI bubble with steps shows "Worked through N steps" text', () => {
  const steps: StepItem[] = [
    { stepKey: 'd1', title: 'Step 1', description: null, status: 'SUCCESS' },
    { stepKey: 'd2', title: 'Step 2', description: null, status: 'SUCCESS' },
    { stepKey: 'd3', title: 'Step 3', description: null, status: 'SUCCESS' },
    { stepKey: 'd4', title: 'Step 4', description: null, status: 'SUCCESS' },
  ];
  render(<MessageBubble sender="AI" text="done" steps={steps} />);
  expect(screen.getByText(/Worked through 4 steps/i)).toBeInTheDocument();
});

test('AI bubble with artifact shows artifact.title directly on the card', () => {
  render(
    <MessageBubble
      sender="AI"
      text="Result ready"
      artifact={{ artifactId: 'art-1', title: 'Version 2' }}
    />,
  );
  // artifact.title is rendered directly — no version computation in the bubble.
  expect(screen.getByText('Version 2')).toBeInTheDocument();
  expect(screen.getByText(/shown right/i)).toBeInTheDocument();
});

test('AI bubble with streaming shows "Working on it"', () => {
  render(<MessageBubble sender="AI" text="" streaming={true} />);
  expect(screen.getByText(/Working on it/i)).toBeInTheDocument();
});

test('artifact card click invokes onArtifactClick with the artifact object', async () => {
  const onArtifactClick = vi.fn();
  render(
    <MessageBubble
      sender="AI"
      text="Result ready"
      artifact={{ artifactId: 'art-1', title: 'Version 1' }}
      onArtifactClick={onArtifactClick}
    />,
  );
  await userEvent.click(screen.getByText('Version 1'));
  expect(onArtifactClick).toHaveBeenCalledTimes(1);
  expect(onArtifactClick).toHaveBeenCalledWith({ artifactId: 'art-1', title: 'Version 1' });
});

// ── Artifact card title display ───────────────────────────────────────────────

test('artifactCard_showsArtifactTitleDirectly_versionPrefixTitle', () => {
  render(
    <MessageBubble
      sender="AI"
      text="done"
      artifact={{ artifactId: 'art-1', title: 'Version 3' }}
    />,
  );
  // artifact.title ("Version 3") is displayed as-is — no computed label.
  expect(screen.getByText('Version 3')).toBeInTheDocument();
});

test('artifactCard_showsArtifactTitleDirectly_customTitle', () => {
  render(
    <MessageBubble
      sender="AI"
      text="done"
      artifact={{ artifactId: 'art-1', title: 'My Custom Dashboard' }}
    />,
  );
  expect(screen.getByText('My Custom Dashboard')).toBeInTheDocument();
});

test('artifactCard_noHoverTooltip_titleIsVisibleText', () => {
  // artifact.title is shown as the card label, not hidden in a title attribute.
  const { container } = render(
    <MessageBubble
      sender="AI"
      text="done"
      artifact={{ artifactId: 'art-1', title: 'Version 1' }}
    />,
  );
  // No title attribute on the button (tooltip removed)
  const cardButton = container.querySelector('button[title]');
  expect(cardButton).toBeNull();
});

// ── Markdown rendering tests ──────────────────────────────────────────────────

test('AI bubble renders **bold** as <strong>', () => {
  const { container } = render(<MessageBubble sender="AI" text="**bold text**" />);
  expect(container.querySelector('strong')).not.toBeNull();
  expect(container.querySelector('strong')?.textContent).toBe('bold text');
});

test('AI bubble renders ## heading as heading element', () => {
  const { container } = render(<MessageBubble sender="AI" text="## Section Title" />);
  expect(container.querySelector('h2')).not.toBeNull();
  expect(container.querySelector('h2')?.textContent).toBe('Section Title');
});

test('AI bubble renders - list items as <li> elements', () => {
  const { container } = render(
    <MessageBubble sender="AI" text={'- item one\n- item two\n- item three'} />,
  );
  const items = container.querySelectorAll('li');
  expect(items.length).toBe(3);
  expect(items[0].textContent).toBe('item one');
});

test('USER bubble renders **bold** as literal asterisks, not <strong>', () => {
  const { container } = render(<MessageBubble sender="USER" text="**bold**" />);
  expect(container.querySelector('strong')).toBeNull();
  expect(screen.getByText('**bold**')).toBeInTheDocument();
});

test('AI streaming liveText renders **bold** as <strong>', () => {
  const { container } = render(
    <MessageBubble sender="AI" text="**streaming bold**" streaming={true} />,
  );
  expect(container.querySelector('strong')).not.toBeNull();
  expect(container.querySelector('strong')?.textContent).toBe('streaming bold');
});

test('AI bubble markdown link opens in new tab with noopener', () => {
  const { container } = render(<MessageBubble sender="AI" text="[link](https://example.com)" />);
  const a = container.querySelector('a');
  expect(a).not.toBeNull();
  expect(a?.getAttribute('href')).toBe('https://example.com');
  expect(a?.getAttribute('target')).toBe('_blank');
  expect(a?.getAttribute('rel')).toBe('noopener noreferrer');
});

// ── Dynamic (d*) step tests ───────────────────────────────────────────────────

test('AI bubble shows "Worked through N steps" for whatever steps are passed', () => {
  // MessageBubble renders the steps it receives; MessageList performs d*-only filtering.
  const steps: StepItem[] = [
    { stepKey: 'd1', title: 'Dynamic Step 1', description: null, status: 'SUCCESS' },
    { stepKey: 'd2', title: 'Dynamic Step 2', description: null, status: 'SUCCESS' },
  ];
  render(<MessageBubble sender="AI" text="done" steps={steps} />);
  expect(screen.getByText(/Worked through 2 steps/i)).toBeInTheDocument();
});

test('AI bubble passes all d* steps to StepChain', () => {
  const steps: StepItem[] = [
    { stepKey: 'd1', title: 'Dyn A', description: null, status: 'RUNNING' },
    { stepKey: 'd2', title: 'Dyn B', description: null, status: 'SUCCESS' },
  ];
  render(<MessageBubble sender="AI" text="live" steps={steps} streaming={true} />);
  const chain = screen.getByTestId('step-chain');
  expect(chain.textContent).toContain('Dyn A');
  expect(chain.textContent).toContain('Dyn B');
});

// ── Thinking toggle integration ───────────────────────────────────────────────

test('AI bubble shows "Working on it…" header when thinking is non-empty (streaming)', () => {
  render(<MessageBubble sender="AI" text="answer" thinking="some reasoning" streaming={true} />);
  expect(screen.getByText(/Working on it/i)).toBeInTheDocument();
});

test('AI bubble shows "Working on it…" header even after streaming ends when thinking is present', () => {
  render(<MessageBubble sender="AI" text="answer" thinking="some reasoning" streaming={false} />);
  expect(screen.getByText(/Working on it/i)).toBeInTheDocument();
});

test('AI bubble does NOT show "Working on it…" when not streaming and thinking is empty', () => {
  render(<MessageBubble sender="AI" text="answer" thinking={null} streaming={false} />);
  expect(screen.queryByText(/Working on it/i)).toBeNull();
});

test('AI bubble thinking content is hidden by default (collapsed)', () => {
  render(<MessageBubble sender="AI" text="answer" thinking="some reasoning" streaming={true} />);
  expect(screen.queryByText('some reasoning')).not.toBeInTheDocument();
});

test('AI bubble "Working on it…" click expands reasoning content when thinking is non-empty', async () => {
  const user = userEvent.setup();
  render(<MessageBubble sender="AI" text="" thinking="reasoning content" streaming={true} />);
  await user.click(screen.getByRole('button', { name: /Working on it/i }));
  expect(screen.getByText('reasoning content')).toBeInTheDocument();
});

test('AI bubble "Working on it…" click collapses reasoning on second click', async () => {
  const user = userEvent.setup();
  render(<MessageBubble sender="AI" text="" thinking="reasoning content" streaming={true} />);
  await user.click(screen.getByRole('button', { name: /Working on it/i }));
  expect(screen.getByText('reasoning content')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: /Working on it/i }));
  expect(screen.queryByText('reasoning content')).not.toBeInTheDocument();
});

test('AI bubble "Working on it…" has chevron indicator when thinking is non-empty', () => {
  render(<MessageBubble sender="AI" text="" thinking="some reasoning" streaming={true} />);
  // The header is rendered as a clickable button (not a plain div) when thinking is present
  expect(screen.getByRole('button', { name: /Working on it/i })).toBeInTheDocument();
});

test('AI bubble "Working on it…" is NOT a button (not expandable) when thinking is empty', () => {
  render(<MessageBubble sender="AI" text="" thinking={null} streaming={true} />);
  // No clickable button for "Working on it" — it's a plain display row
  expect(screen.queryByRole('button', { name: /Working on it/i })).toBeNull();
  // But the text is still visible during streaming
  expect(screen.getByText(/Working on it/i)).toBeInTheDocument();
});

test('AI bubble reasoning content area uses monospace font when expanded', async () => {
  const user = userEvent.setup();
  const { container } = render(
    <MessageBubble sender="AI" text="" thinking="code text" streaming={true} />,
  );
  await user.click(screen.getByRole('button', { name: /Working on it/i }));
  const contentDiv = container.querySelector('[style*="monospace"]');
  expect(contentDiv).not.toBeNull();
});

// ── QuestionCards integration ─────────────────────────────────────────────────

test('AI bubble renders QuestionCards when questions are provided', () => {
  const questions: Question[] = [
    { text: 'Pick a chart?', options: ['Bar', 'Line'], multiSelect: false },
  ];
  const onAnswer = vi.fn();
  render(
    <MessageBubble sender="AI" text="Which chart?" questions={questions} onAnswer={onAnswer} />,
  );
  expect(screen.getByTestId('question-cards')).toBeInTheDocument();
});

test('AI bubble does NOT render QuestionCards when questions is null', () => {
  render(<MessageBubble sender="AI" text="no questions" questions={null} onAnswer={vi.fn()} />);
  expect(screen.queryByTestId('question-cards')).toBeNull();
});

test('AI bubble passes questionsDisabled to QuestionCards', () => {
  const questions: Question[] = [{ text: 'Pick?', options: ['A', 'B'], multiSelect: false }];
  render(
    <MessageBubble
      sender="AI"
      text="pick"
      questions={questions}
      questionsDisabled={true}
      onAnswer={vi.fn()}
    />,
  );
  const cards = screen.getByTestId('question-cards');
  expect(cards.getAttribute('data-disabled')).toBe('true');
});

test('AI bubble renders disabled QuestionCards even when onAnswer is not provided (history-only mode)', () => {
  // Fix 4: questionsDisabled=true alone is sufficient to render disabled cards;
  // onAnswer is not required when the bubble is purely read-only (history).
  const questions: Question[] = [
    { text: 'History pick?', options: ['X', 'Y'], multiSelect: false },
  ];
  render(
    <MessageBubble
      sender="AI"
      text="history"
      questions={questions}
      questionsDisabled={true}
      // onAnswer intentionally omitted
    />,
  );
  const cards = screen.getByTestId('question-cards');
  expect(cards).toBeInTheDocument();
  expect(cards.getAttribute('data-disabled')).toBe('true');
});

// ── fileNames / live streaming layout ─────────────────────────────────────────

test('AI streaming bubble shows file names row when fileNames are provided', () => {
  render(
    <MessageBubble sender="AI" text="" streaming={true} fileNames={['lots.csv', 'defect.csv']} />,
  );
  expect(screen.getByText(/使用檔案/)).toBeInTheDocument();
  expect(screen.getByText(/lots\.csv/)).toBeInTheDocument();
  expect(screen.getByText(/defect\.csv/)).toBeInTheDocument();
});

test('AI streaming bubble does NOT show file names row when fileNames is empty', () => {
  render(<MessageBubble sender="AI" text="" streaming={true} fileNames={[]} />);
  expect(screen.queryByText(/使用檔案/)).toBeNull();
});

test('AI streaming bubble does NOT show file names row when fileNames is absent', () => {
  render(<MessageBubble sender="AI" text="" streaming={true} />);
  expect(screen.queryByText(/使用檔案/)).toBeNull();
});

test('AI streaming bubble with d* steps shows StepChain but NOT "Worked through N steps"', () => {
  const steps: StepItem[] = [
    { stepKey: 'd1', title: 'Dyn Step', description: null, status: 'RUNNING' },
  ];
  render(<MessageBubble sender="AI" text="" streaming={true} steps={steps} />);
  expect(screen.getByTestId('step-chain')).toBeInTheDocument();
  expect(screen.queryByText(/Worked through/i)).toBeNull();
});

test('AI non-streaming bubble with steps shows "Worked through N steps" toggle', () => {
  const steps: StepItem[] = [
    { stepKey: 'd1', title: 'Dyn Step', description: null, status: 'SUCCESS' },
  ];
  render(<MessageBubble sender="AI" text="done" steps={steps} />);
  expect(screen.getByText(/Worked through 1 steps/i)).toBeInTheDocument();
  expect(screen.queryByText(/Working on it/i)).toBeNull();
});

test('AI non-streaming bubble does NOT show file names row even when fileNames provided', () => {
  render(<MessageBubble sender="AI" text="done" fileNames={['data.csv']} />);
  expect(screen.queryByText(/使用檔案/)).toBeNull();
});

// ── HTML source viewer ────────────────────────────────────────────────────────

test('HTML viewer: toggle button is visible when artifact is present', () => {
  render(
    <MessageBubble
      sender="AI"
      text="done"
      artifact={{ artifactId: 'art-1', title: 'Dashboard' }}
    />,
  );
  expect(screen.getByText(/查看 HTML/i)).toBeInTheDocument();
});

test('HTML viewer: toggle button is NOT rendered when no artifact', () => {
  render(<MessageBubble sender="AI" text="done" />);
  expect(screen.queryByText(/查看 HTML/i)).toBeNull();
});

test('HTML viewer: collapsed by default — content not visible', () => {
  render(
    <MessageBubble
      sender="AI"
      text="done"
      artifact={{ artifactId: 'art-1', title: 'Dashboard' }}
    />,
  );
  expect(screen.queryByPlaceholderText('請輸入...')).toBeNull();
  // pre/code block not in DOM yet
  expect(screen.queryByRole('code')).toBeNull();
});

test('HTML viewer: expand triggers fetchArtifactRawHtml with correct id', async () => {
  vi.mocked(fetchArtifactRawHtml).mockResolvedValue('<html>hello</html>');
  const user = userEvent.setup();

  render(
    <MessageBubble
      sender="AI"
      text="done"
      artifact={{ artifactId: 'art-42', title: 'Dashboard' }}
    />,
  );

  await user.click(screen.getByText(/查看 HTML/i));
  expect(fetchArtifactRawHtml).toHaveBeenCalledWith('art-42', expect.any(AbortSignal));
});

test('HTML viewer: shows HTML content in pre/code after successful fetch', async () => {
  vi.mocked(fetchArtifactRawHtml).mockResolvedValue('<html>hello world</html>');
  const user = userEvent.setup();

  render(
    <MessageBubble
      sender="AI"
      text="done"
      artifact={{ artifactId: 'art-1', title: 'Dashboard' }}
    />,
  );

  await user.click(screen.getByText(/查看 HTML/i));
  expect(await screen.findByText('<html>hello world</html>')).toBeInTheDocument();
});

test('HTML viewer: shows "無法載入" when fetch fails (404)', async () => {
  vi.mocked(fetchArtifactRawHtml).mockRejectedValue(new Error('Failed to fetch raw HTML: 404'));
  const user = userEvent.setup();

  render(
    <MessageBubble
      sender="AI"
      text="done"
      artifact={{ artifactId: 'art-missing', title: 'Dashboard' }}
    />,
  );

  await user.click(screen.getByText(/查看 HTML/i));
  expect(await screen.findByText(/無法載入/)).toBeInTheDocument();
});

// ── codeText / live HTML panel ─────────────────────────────────────────────

test('streaming bubble with codeText shows "產生中的 HTML" row in the steps area', () => {
  render(<MessageBubble sender="AI" text="" streaming={true} codeText="<div>hello</div>" />);
  expect(screen.getByText(/產生中的 HTML/)).toBeInTheDocument();
});

test('streaming bubble with codeText: content collapsed by default', () => {
  render(<MessageBubble sender="AI" text="" streaming={true} codeText="<div>hello</div>" />);
  expect(screen.queryByText('<div>hello</div>')).not.toBeInTheDocument();
});

test('streaming bubble with codeText: expand shows the codeText content in pre', async () => {
  const user = userEvent.setup();
  render(<MessageBubble sender="AI" text="" streaming={true} codeText="<div>live content</div>" />);
  await user.click(screen.getByText(/產生中的 HTML/));
  expect(screen.getByText('<div>live content</div>')).toBeInTheDocument();
});

test('non-streaming live bubble with codeText: label is "</> HTML" (not 產生中)', () => {
  render(<MessageBubble sender="AI" text="answer" streaming={false} codeText="<div>hello</div>" />);
  expect(screen.getByText('</> HTML')).toBeInTheDocument();
  expect(screen.queryByText(/產生中/)).toBeNull();
});

test('bubble with codeText AND artifact: only ONE </> row rendered (no 查看 HTML fetch row)', () => {
  render(
    <MessageBubble
      sender="AI"
      text="done"
      artifact={{ artifactId: 'art-1', title: 'Dashboard' }}
      codeText="<div>code</div>"
    />,
  );
  // The live panel should be present
  expect(screen.getByText('</> HTML')).toBeInTheDocument();
  // The fetch viewer should NOT be rendered when codeText is present
  expect(screen.queryByText(/查看 HTML/)).toBeNull();
});

// ── stopped indicator ──────────────────────────────────────────────────────────

test('AI bubble with stopped=true shows "⏹ 已停止生成" indicator', () => {
  render(<MessageBubble sender="AI" text="partial" stopped={true} />);
  expect(screen.getByText(/⏹ 已停止生成/)).toBeInTheDocument();
});

test('AI bubble with stopped=true does NOT show "Working on it…" header', () => {
  render(<MessageBubble sender="AI" text="" streaming={true} stopped={true} />);
  expect(screen.queryByText(/Working on it/i)).toBeNull();
});

test('AI bubble without stopped does NOT show "⏹ 已停止生成" indicator', () => {
  render(<MessageBubble sender="AI" text="done" stopped={false} />);
  expect(screen.queryByText(/⏹ 已停止生成/)).toBeNull();
});

// ── networkError indicator ─────────────────────────────────────────────────────

test('AI bubble with networkError=true shows "⚠ 連線中斷" indicator', () => {
  render(<MessageBubble sender="AI" text="" networkError={true} />);
  expect(screen.getByText(/⚠ 連線中斷，請重新送出一次/)).toBeInTheDocument();
});

test('AI bubble without networkError does NOT show "⚠ 連線中斷" indicator', () => {
  render(<MessageBubble sender="AI" text="done" networkError={false} />);
  expect(screen.queryByText(/⚠ 連線中斷/)).toBeNull();
});

// ── interrupted message rendering ─────────────────────────────────────────────

test('AI bubble with new interrupted text renders gray hint, not markdown', () => {
  render(<MessageBubble sender="AI" text="回應已中斷，請重新送出以繼續" />);
  // Must appear as plain gray text
  expect(screen.getByText('回應已中斷，請重新送出以繼續')).toBeInTheDocument();
  // Must not include parentheses in the displayed text
  expect(screen.queryByText(/（回應已中斷/)).toBeNull();
});

test('AI bubble with legacy interrupted text (parentheses) also renders gray hint', () => {
  render(<MessageBubble sender="AI" text="（回應已中斷，請重新送出以繼續）" />);
  // The bubble normalises both forms to the no-parentheses display text
  expect(screen.getByText('回應已中斷，請重新送出以繼續')).toBeInTheDocument();
});

// ── repair-record rendering ────────────────────────────────────────────────────

test('AI bubble with repair success prefix renders gray hint with tool icon, not markdown', () => {
  const repairText = '已修復儀表板執行錯誤（2 個）：uncaught type error';
  const { container } = render(<MessageBubble sender="AI" text={repairText} />);
  // Text must be visible
  expect(screen.getByText(repairText)).toBeInTheDocument();
  // Must NOT be routed through ReactMarkdown (which would wrap plain text in <p>)
  expect(container.querySelector('p')).toBeNull();
  // Must be rendered inside a gray-text hint container
  const hintDiv = container.querySelector('.text-xs.text-gray-500');
  expect(hintDiv).not.toBeNull();
  expect(hintDiv?.textContent).toContain(repairText);
});

test('AI bubble with repair failure prefix renders gray hint with tool icon, not markdown', () => {
  const repairText = '儀表板執行錯誤自動修復未成功（1 個）：ReferenceError: chart is not defined';
  const { container } = render(<MessageBubble sender="AI" text={repairText} />);
  // Text must be visible
  expect(screen.getByText(repairText)).toBeInTheDocument();
  // Must NOT be routed through ReactMarkdown
  expect(container.querySelector('p')).toBeNull();
  // Must be rendered inside a gray-text hint container
  const hintDiv = container.querySelector('.text-xs.text-gray-500');
  expect(hintDiv).not.toBeNull();
  expect(hintDiv?.textContent).toContain(repairText);
});

// ── tables (live-only TABLE events, forwarded to StepChain) ─────────────────

const TABLE_1: TableResult = {
  tableId: 'tbl_1',
  intent: '計算各機台的不良率',
  columns: ['machine_id', 'defect_rate'],
  rows: [['M1', 0.02]],
  truncated: false,
};

const TABLE_2: TableResult = {
  tableId: 'tbl_2',
  intent: '找出離群值',
  columns: ['lot_id', 'value'],
  rows: [['L1', 5.5]],
  truncated: true,
};

const ONE_STEP: StepItem[] = [
  { stepKey: 'tool_run_sql_r1', title: 'Step', description: null, status: 'SUCCESS' },
];

test('AI bubble forwards every non-referenced accumulated table to StepChain, in arrival order', () => {
  render(
    <MessageBubble
      sender="AI"
      text="done"
      steps={ONE_STEP}
      tables={[TABLE_1, TABLE_2]}
      streaming={true}
    />,
  );
  const stepChain = screen.getByTestId('step-chain');
  expect(stepChain.getAttribute('data-table-ids')).toBe('tbl_1,tbl_2');
  // Neither table is inlined as a full ResultTable — StepChain owns collapsed rendering.
  expect(screen.queryByTestId('result-table')).toBeNull();
});

test('AI bubble forwards no tables to StepChain when tables is undefined', () => {
  render(<MessageBubble sender="AI" text="done" steps={ONE_STEP} streaming={true} />);
  expect(screen.getByTestId('step-chain').getAttribute('data-table-ids')).toBe('');
});

test('AI bubble forwards no tables to StepChain when tables is an empty array', () => {
  render(<MessageBubble sender="AI" text="done" steps={ONE_STEP} tables={[]} streaming={true} />);
  expect(screen.getByTestId('step-chain').getAttribute('data-table-ids')).toBe('');
});

// ── [[table:id]] inline markers in the answer ───────────────────────────────

test('answer with a valid [[table:id]] marker renders the full ResultTable inline and excludes it from what StepChain gets', () => {
  render(
    <MessageBubble
      sender="AI"
      text={`前情提要\n\n[[table:tbl_1]]\n\n後續說明`}
      steps={ONE_STEP}
      tables={[TABLE_1, TABLE_2]}
      streaming={true}
    />,
  );
  // TABLE_1 is referenced by the marker -- rendered inline as a full ResultTable.
  const resultTables = screen.getAllByTestId('result-table');
  expect(resultTables).toHaveLength(1);
  expect(resultTables[0].textContent).toBe(TABLE_1.intent);
  // Only TABLE_2 (unreferenced) is forwarded to StepChain for the per-step collapsed display.
  expect(screen.getByTestId('step-chain').getAttribute('data-table-ids')).toBe('tbl_2');
  // Raw marker text must never reach the DOM.
  expect(screen.queryByText(/\[\[table:/)).toBeNull();
});

test('a mid-text marker splits the answer into text-segment, table, text-segment', () => {
  const { container } = render(
    <MessageBubble
      sender="AI"
      text={`前情提要\n\n[[table:tbl_1]]\n\n後續說明`}
      tables={[TABLE_1]}
    />,
  );
  expect(screen.getByText('前情提要')).toBeInTheDocument();
  expect(screen.getByTestId('result-table')).toBeInTheDocument();
  expect(screen.getByText('後續說明')).toBeInTheDocument();
  // The table node sits between the two text segments in document order.
  const bubble = container.querySelector('.bg-gray-100');
  const rendered = Array.from(bubble?.querySelectorAll('p, [data-testid="result-table"]') ?? []);
  const renderedOrder = rendered.map((node) =>
    node.getAttribute('data-testid') === 'result-table' ? 'table' : node.textContent,
  );
  expect(renderedOrder).toEqual(['前情提要', 'table', '後續說明']);
});

test('a marker with an unknown tableId is silently dropped -- no raw marker text in the DOM', () => {
  render(
    <MessageBubble
      sender="AI"
      text={`結果如下\n\n[[table:tbl_unknown]]\n\n完畢`}
      tables={[TABLE_1]}
    />,
  );
  expect(screen.getByText('結果如下')).toBeInTheDocument();
  expect(screen.getByText('完畢')).toBeInTheDocument();
  expect(screen.queryByTestId('result-table')).toBeNull();
  expect(screen.queryByText(/\[\[table:/)).toBeNull();
});

test('a history bubble (no tables prop) with a marker drops it silently -- TABLE events are live-only (decision 5)', () => {
  render(<MessageBubble sender="AI" text={`結果如下\n\n[[table:tbl_1]]\n\n完畢`} />);
  expect(screen.getByText('結果如下')).toBeInTheDocument();
  expect(screen.getByText('完畢')).toBeInTheDocument();
  expect(screen.queryByTestId('result-table')).toBeNull();
  expect(screen.queryByText(/\[\[table:/)).toBeNull();
});

// ── referencedTables fallback (persisted history tables) ───────────────────────

test('history bubble with referencedTables containing the marker id renders it inline', () => {
  render(
    <MessageBubble
      sender="AI"
      text={`結果如下\n\n[[table:tbl_1]]\n\n完畢`}
      referencedTables={[TABLE_1]}
    />,
  );
  const resultTable = screen.getByTestId('result-table');
  expect(resultTable.textContent).toBe(TABLE_1.intent);
  expect(screen.queryByText(/\[\[table:/)).toBeNull();
});

test('history bubble with referencedTables NOT containing the marker id drops it silently', () => {
  render(
    <MessageBubble
      sender="AI"
      text={`結果如下\n\n[[table:tbl_missing]]\n\n完畢`}
      referencedTables={[TABLE_1]}
    />,
  );
  expect(screen.queryByTestId('result-table')).toBeNull();
  expect(screen.queryByText(/\[\[table:/)).toBeNull();
});

test('live tables takes precedence over referencedTables when both are present', () => {
  const liveVersion: TableResult = { ...TABLE_1, intent: 'LIVE VERSION' };
  render(
    <MessageBubble
      sender="AI"
      text={`結果如下\n\n[[table:tbl_1]]\n\n完畢`}
      tables={[liveVersion]}
      referencedTables={[TABLE_1]}
    />,
  );
  expect(screen.getByTestId('result-table').textContent).toBe('LIVE VERSION');
});

// Live→history rerender must keep the inline table: referencedTablesJson persists it once
// the live `tables` prop resets to null so the marker doesn't lose its data mid-transition.
test('rerender from live (tables) to history (referencedTables) keeps the inline table with no gap', () => {
  const answerText = `分析結果：[[table:tbl_1]]`;
  const { rerender } = render(
    <MessageBubble sender="AI" text={answerText} tables={[TABLE_1]} streaming={false} />,
  );
  expect(screen.getByTestId('result-table')).toBeInTheDocument();

  // Live state resets to null (ChatPanel's post-stream reset()); the tail history message now
  // carries the persisted referencedTables for the identical answer text.
  rerender(<MessageBubble sender="AI" text={answerText} referencedTables={[TABLE_1]} />);

  expect(screen.getByTestId('result-table')).toBeInTheDocument();
  expect(screen.queryByText(/\[\[table:/)).toBeNull();
});

// ── GFM markdown table styling ──────────────────────────────────────────────

test('AI markdown table renders with borders and horizontal-scroll container', () => {
  const markdownTable = ['| 系統 | 工單數 |', '| --- | --- |', '| CRM | 42 |', '| ERP | 17 |'].join(
    '\n',
  );
  const { container } = render(<MessageBubble sender="AI" text={markdownTable} />);

  // remark-gfm 解析出真正的 table 元素，且格線/表頭樣式已套上
  const headerCell = screen.getByRole('columnheader', { name: '系統' });
  expect(headerCell.className).toContain('border');
  expect(headerCell.className).toContain('bg-gray-50');
  const dataCell = screen.getByRole('cell', { name: 'CRM' });
  expect(dataCell.className).toContain('border');
  // 表格外包 overflow-x-auto 容器，寬表格不撐破氣泡
  const scrollContainer = container.querySelector('.overflow-x-auto table');
  expect(scrollContainer).not.toBeNull();
});
