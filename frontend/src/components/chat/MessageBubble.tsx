import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { INTERRUPTED_TEXTS, REPAIR_RECORD_PREFIXES } from '@/constants/messages';
import {
  ThunderboltOutlined,
  CheckCircleFilled,
  LoadingOutlined,
  AppstoreOutlined,
  DownOutlined,
  UpOutlined,
  ToolOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import StepChain from './StepChain';
import QuestionCards from './QuestionCards';
import HtmlCodePanel from './HtmlCodePanel';
import ResultTable from './ResultTable';
import { splitAnswerByTableMarkers, extractReferencedTableIds } from '@/utils/tableMarkers';
import { formatDuration } from '@/utils/formatDuration';
import type { Question, StepItem, TableResult } from '@/types';

const AI_MARKDOWN_CLASSES =
  '[&_p]:my-1 [&_ul]:my-1 [&_ul]:pl-4 [&_li]:list-disc [&_ol]:my-1 [&_ol]:pl-4 [&_ol>li]:list-decimal [&_h1]:text-base [&_h1]:font-semibold [&_h1]:my-1 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:my-1 [&_h3]:text-sm [&_h3]:font-medium [&_h3]:my-1 [&_strong]:font-semibold [&_code]:bg-gray-200 [&_code]:px-1 [&_code]:rounded [&_pre]:bg-gray-200 [&_pre]:rounded [&_pre]:p-2 [&_pre]:my-1 [&_pre]:overflow-x-auto';

export interface Props {
  sender: 'USER' | 'AI';
  text: string;
  steps?: StepItem[] | null;
  artifact?: { artifactId: string; title: string } | null;
  streaming?: boolean;
  /** True when the user explicitly stopped generation; shows "⏹ 已停止生成" indicator. */
  stopped?: boolean;
  /** True when an unexpected network disconnection occurred; shows "⚠ 連線中斷" indicator. */
  networkError?: boolean;
  onArtifactClick?: (a: { artifactId: string; title: string }) => void;
  /** Accumulated reasoning text (only passed for the live bubble; not persisted in history). */
  thinking?: string | null;
  /** Questions from the agent (shown after streaming ends, or as disabled in history). */
  questions?: Question[] | null;
  /** When true, QuestionCards are rendered in a disabled (read-only) state. */
  questionsDisabled?: boolean;
  /** Callback when the user answers a question card. */
  onAnswer?: (text: string) => void;
  /** Non-expired file names for the active session — shown as a small row during streaming. */
  fileNames?: string[];
  /** Accumulated live HTML code from CODE SSE events. When non-empty, renders the live
   *  HTML panel in the steps area instead of the tail lazy-fetch viewer. */
  codeText?: string | null;
  /** TABLE events from the live stream (live-only, empty for history bubbles); ones the answer
   *  does NOT reference via a `[[table:id]]` marker go to {@link StepChain} collapsed. */
  tables?: TableResult[];
  /** Tables the answer referenced via a `[[table:id]]` marker, persisted on the message — the
   *  history counterpart to `tables`, used as fallback marker resolution when it's absent. */
  referencedTables?: TableResult[] | null;
  /** Elapsed ms of the turn that produced this bubble; shown as a footer after streaming ends. */
  durationMs?: number | null;
  /** Epoch ms the live turn started; drives the ticking timer while streaming. */
  timerStartedAt?: number | null;
}

const MessageBubble: React.FC<Props> = ({
  sender,
  text,
  steps,
  artifact,
  streaming,
  stopped = false,
  networkError = false,
  onArtifactClick,
  thinking,
  questions,
  questionsDisabled = false,
  onAnswer,
  fileNames,
  codeText,
  tables,
  referencedTables,
  durationMs,
  timerStartedAt,
}) => {
  const [stepsExpanded, setStepsExpanded] = useState(!!streaming);
  const [thinkingExpanded, setThinkingExpanded] = useState(false);
  const thinkingContentRef = useRef<HTMLDivElement>(null);
  const [elapsedMs, setElapsedMs] = useState(0);

  // Ticks the live turn timer every second while streaming; resets when timerStartedAt changes.
  useEffect(() => {
    if (!streaming || timerStartedAt == null) {
      return undefined;
    }
    setElapsedMs(Date.now() - timerStartedAt);
    const intervalId = setInterval(() => {
      setElapsedMs(Date.now() - timerStartedAt);
    }, 1000);
    return () => clearInterval(intervalId);
  }, [streaming, timerStartedAt]);

  const toggleSteps = useCallback(() => {
    setStepsExpanded((prev) => !prev);
  }, []);

  const toggleThinking = useCallback(() => {
    setThinkingExpanded((prev) => !prev);
  }, []);

  const handleArtifactClick = useCallback(() => {
    if (artifact && onArtifactClick) {
      onArtifactClick(artifact);
    }
  }, [artifact, onArtifactClick]);

  // Auto-scroll thinking content to bottom as new tokens arrive
  useEffect(() => {
    if (thinkingExpanded && thinkingContentRef.current) {
      thinkingContentRef.current.scrollTop = thinkingContentRef.current.scrollHeight;
    }
  }, [thinking, thinkingExpanded]);

  // Tables the answer pulls inline via a `[[table:id]]` marker are excluded from the
  // collapsed per-step tables StepChain renders — they're shown in full inline instead.
  const referencedTableIds = useMemo(() => extractReferencedTableIds(text ?? ''), [text]);
  // Falls back from live `tables` to persisted `referencedTables` once the stream ends, so
  // the resolved table doesn't flicker away.
  const markerTableSource = tables ?? referencedTables ?? undefined;
  const answerSegments = useMemo(
    () => splitAnswerByTableMarkers(text ?? '', markerTableSource),
    [text, markerTableSource],
  );
  // Non-referenced tables go to StepChain, rendered collapsed under the producing step;
  // only ever populated for live bubbles (tables is live-only).
  const intermediateTables = useMemo(
    () => (tables ?? []).filter((table) => !referencedTableIds.has(table.tableId)),
    [tables, referencedTableIds],
  );

  if (sender === 'USER') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl bg-blue-500 px-4 py-2.5 text-sm text-white">
          {text}
        </div>
      </div>
    );
  }

  // AI bubble
  const hasSteps = steps && steps.length > 0;
  const hasThinking = !!thinking;
  const hasQuestions = questions && questions.length > 0;
  const hasFileNames = fileNames != null && fileNames.length > 0;

  // Show the "Working on it…" header while actively streaming (not when user stopped),
  // or after streaming if thinking content is present (collapsible reasoning section).
  const showWorkingHeader = (streaming && !stopped) || hasThinking;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1 text-[11px] text-gray-400">
        <ThunderboltOutlined style={{ fontSize: 11 }} />
        <span>eRD AI</span>
      </div>
      <div
        className={`max-w-[85%] rounded-2xl bg-gray-100 px-4 py-2.5 text-sm text-gray-800 ${AI_MARKDOWN_CLASSES}`}
      >
        {/* "Working on it…" collapsible header — doubles as the thinking toggle */}
        {showWorkingHeader && (
          <div className="mb-2">
            {hasThinking ? (
              /* Clickable toggle when reasoning content is present */
              <button
                onClick={toggleThinking}
                className="flex w-full cursor-pointer items-center gap-1 text-left text-xs text-gray-500 hover:text-gray-700"
              >
                {streaming && <LoadingOutlined spin style={{ fontSize: 11 }} />}
                <span className="flex-1">Working on it…</span>
                <span className="text-[10px]">{thinkingExpanded ? '▲' : '▼'}</span>
              </button>
            ) : (
              /* Non-interactive display when no reasoning content yet */
              <div className="flex items-center gap-1 text-xs text-gray-500">
                {streaming && <LoadingOutlined spin style={{ fontSize: 11 }} />}
                <span>Working on it…</span>
              </div>
            )}

            {/* Reasoning content — monospace, gray bg, max 240px, auto-scrolled */}
            {thinkingExpanded && hasThinking && (
              <div
                ref={thinkingContentRef}
                className="mt-1 max-h-[240px] overflow-auto rounded bg-gray-200 p-2 text-[11px] text-gray-600"
                style={{ fontFamily: 'monospace' }}
              >
                {thinking}
              </div>
            )}

            {/* File names row stays below the header */}
            {hasFileNames && (
              <div className="mt-0.5 text-[11px] text-gray-400">
                📎 使用檔案：{fileNames!.join('、')}
              </div>
            )}
          </div>
        )}

        {/* Steps section — only shown for d* steps */}
        {hasSteps && (
          <div className="mb-2">
            {/* History: show collapsible "Worked through N steps" toggle */}
            {!streaming && (
              <button
                onClick={toggleSteps}
                className="mb-1 flex w-full cursor-pointer items-center gap-1 text-left text-xs text-gray-500 hover:text-gray-700"
              >
                <CheckCircleFilled className="text-green-500" style={{ fontSize: 12 }} />
                <span>Worked through {steps!.length} steps</span>
                {stepsExpanded ? (
                  <UpOutlined style={{ fontSize: 10 }} />
                ) : (
                  <DownOutlined style={{ fontSize: 10 }} />
                )}
              </button>
            )}
            {(stepsExpanded || streaming) && (
              <StepChain steps={steps!} tables={intermediateTables} />
            )}
          </div>
        )}

        {/* Live HTML code panel — shown in steps area when codeText is present */}
        {codeText && (
          <HtmlCodePanel
            label={streaming ? '</> 產生中的 HTML' : '</> HTML'}
            code={codeText}
            autoScroll={!!streaming}
          />
        )}

        {/* Text — system-record messages render as small gray hints; all other AI replies use markdown */}
        {(() => {
          const isInterrupted = text ? INTERRUPTED_TEXTS.includes(text) : false;
          const isRepairRecord = text
            ? REPAIR_RECORD_PREFIXES.some((prefix) => text.startsWith(prefix))
            : false;

          if (isInterrupted) {
            return <div className="text-xs text-gray-500">回應已中斷，請重新送出以繼續</div>;
          }
          if (isRepairRecord) {
            return (
              <div className="text-xs text-gray-500">
                <ToolOutlined style={{ fontSize: 11, marginRight: 4 }} />
                {text}
              </div>
            );
          }
          // `[[table:id]]` markers split the text so a referenced table renders inline as a
          // full ResultTable; an unresolved marker is dropped by splitAnswerByTableMarkers
          // rather than reaching the DOM as raw marker text.
          return answerSegments.map((segment, segmentIndex) =>
            segment.type === 'table' ? (
              <ResultTable
                key={`table-${segment.table.tableId}-${segmentIndex}`}
                intent={segment.table.intent}
                columns={segment.table.columns}
                rows={segment.table.rows}
                truncated={segment.table.truncated}
              />
            ) : (
              <ReactMarkdown
                key={`text-${segmentIndex}`}
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children }) => (
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                  table: ({ children }) => (
                    <div className="my-2 overflow-x-auto">
                      <table className="w-full border-collapse text-xs [&_tbody_tr:nth-child(even)]:bg-gray-50">
                        {children}
                      </table>
                    </div>
                  ),
                  th: ({ node: _node, children, ...cellProps }) => (
                    <th
                      {...cellProps}
                      className="border border-gray-200 bg-gray-50 px-2 py-1 text-left font-medium text-gray-600"
                    >
                      {children}
                    </th>
                  ),
                  td: ({ node: _node, children, ...cellProps }) => (
                    <td {...cellProps} className="border border-gray-200 px-2 py-1">
                      {children}
                    </td>
                  ),
                }}
              >
                {segment.content}
              </ReactMarkdown>
            ),
          );
        })()}

        {/* Artifact card */}
        {artifact && (
          <button
            onClick={handleArtifactClick}
            className="mt-2 flex w-full cursor-pointer items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-left text-xs hover:bg-gray-50"
          >
            <AppstoreOutlined style={{ fontSize: 13, color: '#1677ff' }} />
            <span className="flex-1 font-medium text-gray-700">{artifact.title}</span>
            <span className="text-gray-400">shown right →</span>
          </button>
        )}

        {/* HTML source viewer — lazy-fetch; only shown when no live codeText is present */}
        {artifact && !codeText && (
          <HtmlCodePanel label="</> 查看 HTML" artifactId={artifact.artifactId} />
        )}

        {/* Question cards — shown at bubble tail; disabled for history messages.
            Render when onAnswer is provided OR the cards are disabled (read-only history). */}
        {hasQuestions && (onAnswer || questionsDisabled) && (
          <QuestionCards
            questions={questions!}
            onAnswer={onAnswer ?? (() => {})}
            disabled={questionsDisabled}
          />
        )}

        {/* Turn timer — ticks while streaming, static once the turn is done */}
        {streaming && timerStartedAt != null && (
          <div className="mt-1 flex items-center gap-1 text-[11px] text-gray-400">
            <ClockCircleOutlined style={{ fontSize: 11 }} />
            <span>{formatDuration(elapsedMs)}</span>
          </div>
        )}
        {!streaming && durationMs != null && (
          <div className="mt-1 flex items-center gap-1 text-[11px] text-gray-400">
            <ClockCircleOutlined style={{ fontSize: 11 }} />
            <span>{formatDuration(durationMs)}</span>
          </div>
        )}

        {/* User-cancelled stop indicator */}
        {stopped && <div className="mt-1 text-[11px] text-gray-400">⏹ 已停止生成</div>}

        {/* Unexpected network disconnection indicator */}
        {networkError && (
          <div className="mt-1 text-[11px] text-orange-500">⚠ 連線中斷，請重新送出一次</div>
        )}
      </div>
    </div>
  );
};

export default React.memo(MessageBubble);
