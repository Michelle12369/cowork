import React, { useEffect, useMemo, useRef } from 'react';
import MessageBubble from './MessageBubble';
import type { Message, AgentStreamState, Question, StepItem, TableResult } from '@/types';

interface Props {
  messages: Message[];
  live: AgentStreamState | null;
  optimisticUserText?: string | null;
  onArtifactClick?: (a: { artifactId: string; title: string }) => void;
  /** Callback when the user answers a question card (live or history). */
  onAnswer?: (text: string) => void;
  /** Whether the live question cards are disabled (user already answered). */
  questionsDisabled?: boolean;
  /** Non-expired file names for the active session — forwarded to the live bubble. */
  fileNames?: string[];
  /** Optional content rendered at the bottom of the scroll container so it scrolls with messages. */
  bottomSlot?: React.ReactNode;
}

function parseSteps(stepsJson: string | null): StepItem[] | null {
  if (!stepsJson) return null;
  try {
    return JSON.parse(stepsJson) as StepItem[];
  } catch {
    return null;
  }
}

/** Normalizes an empty/absent step list to `null`, which `MessageBubble` treats as "no step
 *  chain". */
function stepsOrNull(steps: StepItem[] | null): StepItem[] | null {
  return steps && steps.length > 0 ? steps : null;
}

function parseQuestions(questionsJson: string | null): Question[] | null {
  if (!questionsJson) return null;
  try {
    const parsed = JSON.parse(questionsJson) as Question[];
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : null;
  } catch {
    return null;
  }
}

/** Parses the persisted `[[table:id]]`-referenced tables. Malformed JSON falls back to null
 *  rather than throwing, same defensive posture as parseQuestions. */
function parseReferencedTables(referencedTablesJson: string | null): TableResult[] | null {
  if (!referencedTablesJson) return null;
  try {
    const parsed = JSON.parse(referencedTablesJson) as TableResult[];
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : null;
  } catch {
    return null;
  }
}

const MessageList: React.FC<Props> = ({
  messages,
  live,
  optimisticUserText,
  onArtifactClick,
  onAnswer,
  questionsDisabled = false,
  fileNames,
  bottomSlot,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [messages, live, optimisticUserText, bottomSlot]);

  // Suppress the tail AI history message only once the live bubble's stream has finished, so
  // the same turn isn't rendered twice; during streaming the tail still belongs to the previous turn.
  const displayMessages =
    live != null && !live.isStreaming && messages.at(-1)?.sender === 'AI'
      ? messages.slice(0, -1)
      : messages;

  // Memoize parsed steps and questions for history messages so JSON.parse does not
  // run on every render token during an active stream (O(history × tokens) → O(1)).
  const parsedHistory = useMemo(
    () =>
      displayMessages.map((msg) => ({
        id: msg.id,
        steps: msg.sender === 'AI' ? stepsOrNull(parseSteps(msg.stepsJson)) : null,
        questions: msg.sender === 'AI' ? parseQuestions(msg.questionsJson) : null,
        referencedTables:
          msg.sender === 'AI' ? parseReferencedTables(msg.referencedTablesJson) : null,
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [displayMessages],
  );

  return (
    <div ref={containerRef} className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
      {displayMessages.map((msg, idx) => (
        <MessageBubble
          key={msg.id}
          sender={msg.sender}
          text={msg.text}
          steps={parsedHistory[idx]?.steps}
          artifact={
            msg.artifactId
              ? { artifactId: msg.artifactId, title: msg.artifactTitle ?? msg.text.slice(0, 50) }
              : null
          }
          onArtifactClick={onArtifactClick}
          // thinking is intentionally not passed for history (not persisted, as designed)
          questions={parsedHistory[idx]?.questions}
          questionsDisabled={true}
          onAnswer={onAnswer}
          referencedTables={parsedHistory[idx]?.referencedTables}
        />
      ))}

      {optimisticUserText != null && <MessageBubble sender="USER" text={optimisticUserText} />}

      {live && (
        <MessageBubble
          sender="AI"
          text={live.liveText}
          steps={stepsOrNull(live.steps)}
          artifact={live.artifact}
          streaming={live.isStreaming}
          stopped={live.stopped}
          networkError={live.networkError && !live.isStreaming}
          onArtifactClick={onArtifactClick}
          thinking={live.thinking || null}
          // Show questions only when streaming has ended
          questions={live.isStreaming ? null : live.questions}
          questionsDisabled={questionsDisabled}
          onAnswer={onAnswer}
          fileNames={fileNames}
          codeText={live.codeText || null}
          tables={live.tables}
        />
      )}

      {bottomSlot}
    </div>
  );
};

export default MessageList;
