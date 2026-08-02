/**
 * MessageList tests — live-bubble deduplication: when `live != null` and the last history
 * message is an AI message, `displayMessages` drops that tail entry so the AI turn isn't
 * rendered twice (once as history, once as the live bubble).
 */
import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import MessageList from './MessageList';
import type { Message, AgentStreamState, Question, StepItem, TableResult } from '@/types';

// ── Module mocks ──────────────────────────────────────────────────────────────

vi.mock('./MessageBubble', () => ({
  default: ({
    sender,
    text,
    questions,
    questionsDisabled,
    onAnswer,
    steps,
    fileNames,
    tables,
    referencedTables,
  }: {
    sender: 'USER' | 'AI';
    text: string;
    questions?: Question[] | null;
    questionsDisabled?: boolean;
    onAnswer?: (t: string) => void;
    steps?: StepItem[] | null;
    streaming?: boolean;
    fileNames?: string[];
    tables?: TableResult[];
    referencedTables?: TableResult[] | null;
  }) => (
    <div
      data-testid={`bubble-${sender}`}
      data-text={text}
      data-questions-disabled={questionsDisabled}
      data-has-questions={questions != null && questions.length > 0}
      data-step-keys={(steps ?? []).map((step) => step.stepKey).join(',')}
      data-file-names={(fileNames ?? []).join(',')}
      data-table-ids={(tables ?? []).map((table) => table.tableId).join(',')}
      data-referenced-table-ids={(referencedTables ?? []).map((table) => table.tableId).join(',')}
    >
      {text}
      {questions &&
        questions.map((question, questionIndex) => (
          <button
            key={questionIndex}
            onClick={() => onAnswer?.(question.options[0])}
            disabled={questionsDisabled}
          >
            {question.text}
          </button>
        ))}
    </div>
  ),
}));

// ── Fixtures ──────────────────────────────────────────────────────────────────

const QUESTIONS: Question[] = [
  { text: 'Which chart?', options: ['Bar', 'Line'], multiSelect: false },
];

const QUESTIONS_JSON = JSON.stringify(QUESTIONS);

function makeUserMsg(id: string, text: string): Message {
  return {
    id,
    sender: 'USER',
    text,
    stepsJson: null,
    artifactId: null,
    artifactTitle: null,
    questionsJson: null,
    referencedTablesJson: null,
    createdAt: '2026-01-01T00:00:00Z',
  };
}

function makeAiMsg(id: string, text: string, questionsJson: string | null = null): Message {
  return {
    id,
    sender: 'AI',
    text,
    stepsJson: null,
    artifactId: null,
    artifactTitle: null,
    questionsJson,
    referencedTablesJson: null,
    createdAt: '2026-01-01T00:00:00Z',
  };
}

const IDLE_LIVE: AgentStreamState = {
  isStreaming: false,
  steps: [],
  liveText: 'Live answer',
  answer: null,
  artifact: null,
  error: null,
  thinking: '',
  questions: QUESTIONS,
  codeText: '',
  stopped: false,
  networkError: false,
  tables: [],
  durationMs: null,
};

// ── Tests ─────────────────────────────────────────────────────────────────────

test('live != null + AI tail → tail history AI bubble is suppressed; only live bubble shows', () => {
  const messages: Message[] = [
    makeUserMsg('u1', 'Hello'),
    makeAiMsg('a1', 'AI reply with questions', QUESTIONS_JSON),
  ];

  render(
    <MessageList
      messages={messages}
      live={IDLE_LIVE}
      onAnswer={vi.fn()}
      questionsDisabled={false}
    />,
  );

  // Only the live AI bubble appears (one with "Live answer")
  const aiBubbles = screen.getAllByTestId('bubble-AI');
  expect(aiBubbles).toHaveLength(1);
  expect(aiBubbles[0].getAttribute('data-text')).toBe('Live answer');
  // The live bubble carries interactive questions
  expect(aiBubbles[0].getAttribute('data-questions-disabled')).toBe('false');
  // The disabled history AI bubble is NOT present
  expect(screen.queryByText('AI reply with questions')).toBeNull();
});

test('live = null + AI tail with questionsJson → history AI bubble renders normally (disabled)', () => {
  const messages: Message[] = [
    makeUserMsg('u1', 'Hello'),
    makeAiMsg('a1', 'AI reply with questions', QUESTIONS_JSON),
  ];

  render(
    <MessageList messages={messages} live={null} onAnswer={vi.fn()} questionsDisabled={true} />,
  );

  // History AI bubble is present with questions disabled
  const aiBubbles = screen.getAllByTestId('bubble-AI');
  expect(aiBubbles).toHaveLength(1);
  expect(aiBubbles[0].getAttribute('data-text')).toBe('AI reply with questions');
});

test('live != null + tail is USER msg → no filtering; history USER and live AI both render', () => {
  // During active streaming the latest history message is the user prompt, not an AI reply.
  // The slice condition (last message.sender === 'AI') must NOT trigger here.
  const messages: Message[] = [
    makeAiMsg('a1', 'Previous AI reply'),
    makeUserMsg('u2', 'New user prompt'),
  ];

  render(
    <MessageList
      messages={messages}
      live={{ ...IDLE_LIVE, isStreaming: true }}
      onAnswer={vi.fn()}
      questionsDisabled={false}
    />,
  );

  // Both history messages rendered
  expect(screen.getByText('Previous AI reply')).toBeInTheDocument();
  expect(screen.getByText('New user prompt')).toBeInTheDocument();
  // Live AI bubble also rendered
  expect(screen.getByText('Live answer')).toBeInTheDocument();
});

test('live != null + AI tail without questions → tail is still suppressed (no duplicate bubbles)', () => {
  // Even when the AI tail has no questionsJson, the live bubble replaces it to avoid duplicate text.
  const messages: Message[] = [makeAiMsg('a1', 'Partial answer')];

  render(
    <MessageList messages={messages} live={{ ...IDLE_LIVE, questions: null }} onAnswer={vi.fn()} />,
  );

  const aiBubbles = screen.getAllByTestId('bubble-AI');
  // Only the live bubble; the history tail is suppressed
  expect(aiBubbles).toHaveLength(1);
  expect(aiBubbles[0].getAttribute('data-text')).toBe('Live answer');
});

test('streaming live + AI tail → previous-turn AI bubble STILL renders (dedup skipped during stream)', () => {
  // During an active stream the history tail is the PREVIOUS round's AI reply, not the current one.
  // The dedup slice must NOT fire (isStreaming === true) so the previous answer remains visible.
  const messages: Message[] = [
    makeUserMsg('u1', 'Hello'),
    makeAiMsg('a1', 'Previous AI reply', QUESTIONS_JSON),
  ];

  render(
    <MessageList
      messages={messages}
      live={{ ...IDLE_LIVE, isStreaming: true }}
      onAnswer={vi.fn()}
      questionsDisabled={false}
    />,
  );

  // The previous-turn AI bubble (history) must remain visible while streaming
  expect(screen.getByText('Previous AI reply')).toBeInTheDocument();
  // Both history AI bubble and live AI bubble are rendered (no suppression during stream)
  const aiBubbles = screen.getAllByTestId('bubble-AI');
  expect(aiBubbles).toHaveLength(2);
  // Live bubble is also present
  expect(screen.getByText('Live answer')).toBeInTheDocument();
});

// Step rendering: stepKey is a plain identifier (spec §16.4-1) — every step in
// stepsJson/live.steps renders regardless of its key, no prefix-based filtering.

function makeStep(stepKey: string, title: string): StepItem {
  return { stepKey, title, description: null, status: 'SUCCESS' };
}

const LEGACY_KEYED_STEPS: StepItem[] = [
  makeStep('s1', 'Reading imported files'),
  makeStep('s2', 'Analyzing data profile'),
  makeStep('s3', 'Generating dashboard'),
  makeStep('s4', 'Rendering dashboard'),
];

const MIXED_STEPS: StepItem[] = [
  ...LEGACY_KEYED_STEPS.slice(0, 3),
  makeStep('d1', '資料讀取'),
  makeStep('d2', '產生儀表板'),
  LEGACY_KEYED_STEPS[3],
];

test('history message with steps of mixed key shapes → every step is shown, in order', () => {
  const msg: Message = {
    ...makeAiMsg('a1', 'Done'),
    stepsJson: JSON.stringify(MIXED_STEPS),
  };

  render(<MessageList messages={[msg]} live={null} />);

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-step-keys')).toBe('s1,s2,s3,d1,d2,s4');
});

test('history message with no steps → empty stepsJson renders no step chain', () => {
  const msg: Message = {
    ...makeAiMsg('a1', 'Done'),
    stepsJson: JSON.stringify([]),
  };

  render(<MessageList messages={[msg]} live={null} />);

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-step-keys')).toBe('');
});

test('live bubble with steps of mixed key shapes → every step is shown, in order', () => {
  render(
    <MessageList
      messages={[]}
      live={{ ...IDLE_LIVE, isStreaming: true, steps: MIXED_STEPS, questions: null }}
    />,
  );

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-step-keys')).toBe('s1,s2,s3,d1,d2,s4');
});

test('live bubble with no steps yet → no step chain', () => {
  render(
    <MessageList
      messages={[]}
      live={{ ...IDLE_LIVE, isStreaming: true, steps: [], questions: null }}
    />,
  );

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-step-keys')).toBe('');
});

test('history message with a step key carrying no d/r prefix → step is still shown', () => {
  // stepKey no longer gates rendering (spec §16.4-1); a non-prefixed key like "analysis" renders too.
  const stepsWithoutPrefix: StepItem[] = [makeStep('analysis', '分析資料中')];
  const msg: Message = {
    ...makeAiMsg('a1', 'Done'),
    stepsJson: JSON.stringify(stepsWithoutPrefix),
  };

  render(<MessageList messages={[msg]} live={null} />);

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-step-keys')).toBe('analysis');
});

test('history message with repair step → r* steps are shown alongside d* steps', () => {
  const stepsWithRepair: StepItem[] = [
    makeStep('d1', '資料讀取'),
    makeStep('r1', 'JS 問題修復完成（2 個）'),
  ];
  const msg: Message = {
    ...makeAiMsg('a1', 'Done'),
    stepsJson: JSON.stringify(stepsWithRepair),
  };

  render(<MessageList messages={[msg]} live={null} />);

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-step-keys')).toBe('d1,r1');
});

test('live bubble with repair step → r1 is shown', () => {
  const liveSteps: StepItem[] = [
    makeStep('d1', '產生儀表板'),
    makeStep('r1', '偵測到 2 個 JS 問題，自動修復中'),
  ];

  render(
    <MessageList
      messages={[]}
      live={{ ...IDLE_LIVE, isStreaming: true, steps: liveSteps, questions: null }}
    />,
  );

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-step-keys')).toBe('d1,r1');
});

// ── fileNames forwarding ──────────────────────────────────────────────────────

test('fileNames are forwarded to the live bubble', () => {
  render(
    <MessageList
      messages={[]}
      live={{ ...IDLE_LIVE, isStreaming: true, steps: [], questions: null }}
      fileNames={['lots.csv', 'defect.csv']}
    />,
  );

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-file-names')).toBe('lots.csv,defect.csv');
});

test('fileNames not forwarded when prop is absent', () => {
  render(
    <MessageList
      messages={[]}
      live={{ ...IDLE_LIVE, isStreaming: true, steps: [], questions: null }}
    />,
  );

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-file-names')).toBe('');
});

test('fileNames are NOT forwarded to history message bubbles', () => {
  const messages: Message[] = [makeAiMsg('a1', 'History AI reply')];
  render(<MessageList messages={messages} live={null} fileNames={['data.csv']} />);

  const bubble = screen.getByTestId('bubble-AI');
  // history bubbles never receive fileNames
  expect(bubble.getAttribute('data-file-names')).toBe('');
});

// ── tables (decision 5: live-only, never in history) ───────────────────────

const LIVE_TABLES: TableResult[] = [
  {
    tableId: 'tbl_1',
    intent: '計算各機台的不良率',
    columns: ['machine_id', 'defect_rate'],
    rows: [['M1', 0.02]],
    truncated: false,
  },
];

test('live tables are forwarded to the live bubble', () => {
  render(
    <MessageList
      messages={[]}
      live={{ ...IDLE_LIVE, isStreaming: true, tables: LIVE_TABLES, questions: null }}
    />,
  );

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-table-ids')).toBe('tbl_1');
});

test('tables are NOT forwarded to history message bubbles (live-only, decision 5)', () => {
  const messages: Message[] = [makeAiMsg('a1', 'History AI reply')];
  render(<MessageList messages={messages} live={null} />);

  const bubble = screen.getByTestId('bubble-AI');
  // history bubbles never receive tables — no persistence for TABLE events
  expect(bubble.getAttribute('data-table-ids')).toBe('');
});

// ── referencedTables (persisted, unlike intermediate `tables`) ─────────────────

test('history message with referencedTablesJson → parsed and forwarded as referencedTables', () => {
  const msg: Message = {
    ...makeAiMsg('a1', 'Here: [[table:tbl_1]]'),
    referencedTablesJson: JSON.stringify(LIVE_TABLES),
  };

  render(<MessageList messages={[msg]} live={null} />);

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-referenced-table-ids')).toBe('tbl_1');
});

test('history message with null referencedTablesJson → referencedTables is not forwarded', () => {
  const messages: Message[] = [makeAiMsg('a1', 'Plain answer, no markers')];
  render(<MessageList messages={messages} live={null} />);

  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-referenced-table-ids')).toBe('');
});

test('history message with malformed referencedTablesJson → does not throw, forwards nothing', () => {
  const msg: Message = {
    ...makeAiMsg('a1', 'Broken'),
    referencedTablesJson: '{not valid json',
  };

  expect(() => render(<MessageList messages={[msg]} live={null} />)).not.toThrow();
  const bubble = screen.getByTestId('bubble-AI');
  expect(bubble.getAttribute('data-referenced-table-ids')).toBe('');
});

// Once the live bubble resets to null, MessageList falls back to the history message directly,
// so referencedTablesJson must carry the same table data or the [[table:id]] marker goes unresolved.
test('live→history transition: referenced table stays inline (no flicker) once history carries it', () => {
  const liveWithTable = {
    ...IDLE_LIVE,
    isStreaming: false,
    liveText: 'Answer: [[table:tbl_1]]',
    tables: LIVE_TABLES,
    questions: null,
  };
  const historyMessage: Message = {
    ...makeAiMsg('a1', 'Answer: [[table:tbl_1]]'),
    referencedTablesJson: JSON.stringify(LIVE_TABLES),
  };

  const { rerender } = render(
    <MessageList messages={[]} live={liveWithTable} onAnswer={vi.fn()} />,
  );
  // While `live` is still set, the live bubble carries the table via `tables`.
  expect(screen.getByTestId('bubble-AI').getAttribute('data-table-ids')).toBe('tbl_1');

  // Transition: `live` resets to null (ChatPanel's post-stream reset()); history now
  // contains the persisted AI message with referencedTablesJson already populated.
  rerender(<MessageList messages={[historyMessage]} live={null} onAnswer={vi.fn()} />);

  // Exactly one AI bubble — no duplicate/flash — and the table id survives via referencedTables.
  const aiBubbles = screen.getAllByTestId('bubble-AI');
  expect(aiBubbles).toHaveLength(1);
  expect(aiBubbles[0].getAttribute('data-referenced-table-ids')).toBe('tbl_1');
});
