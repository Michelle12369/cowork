import React, { useCallback, useMemo, useState } from 'react';
import { EditOutlined } from '@ant-design/icons';
import type { Question } from '@/types';

interface QuestionCardsProps {
  questions: Question[];
  onAnswer: (text: string) => void;
  disabled?: boolean;
}

const OTHER_KEY = '__other__';

const OPTION_BASE = 'rounded-lg border px-3 py-1.5 text-xs transition-colors';
const OPTION_DISABLED = 'cursor-not-allowed border-gray-200 text-gray-400 opacity-50';
const OPTION_IDLE =
  'cursor-pointer border-gray-300 text-gray-700 hover:border-blue-400 hover:bg-blue-50 hover:text-blue-700';
const OPTION_SELECTED = 'border-blue-400 bg-blue-50 text-blue-700';

function optionClasses(selected: boolean, disabled: boolean): string {
  return [
    OPTION_BASE,
    selected && !disabled ? OPTION_SELECTED : '',
    disabled ? OPTION_DISABLED : OPTION_IDLE,
  ]
    .filter(Boolean)
    .join(' ');
}

/** Renders agent clarification questions: immediate mode (one single-select question) submits
 *  on click; form mode (multiple/multi-select) collects all answers behind one 送出 button. */
const QuestionCards: React.FC<QuestionCardsProps> = ({ questions, onAnswer, disabled = false }) => {
  const [selections, setSelections] = useState<ReadonlyArray<ReadonlySet<string>>>(() =>
    questions.map(() => new Set<string>()),
  );
  const [otherTexts, setOtherTexts] = useState<string[]>(() => questions.map(() => ''));
  // Tracks whether the "其他" free-text input is open in immediate mode.
  const [immediateOtherOpen, setImmediateOtherOpen] = useState(false);

  const immediateMode = questions.length === 1 && !questions[0].multiSelect;

  const handleImmediate = useCallback(
    (option: string): void => {
      if (!disabled) {
        onAnswer(option);
      }
    },
    [disabled, onAnswer],
  );

  const handleToggle = useCallback(
    (qIndex: number, option: string, multiSelect: boolean): void => {
      if (disabled) return;
      setSelections((prev) =>
        questions.map((_, questionIndex) => {
          const current = prev[questionIndex] ?? new Set<string>();
          if (questionIndex !== qIndex) return current;
          const next = new Set(current);
          if (multiSelect) {
            if (next.has(option)) {
              next.delete(option);
            } else {
              next.add(option);
            }
          } else {
            // Radio behavior: selecting an option replaces the previous choice.
            next.clear();
            next.add(option);
          }
          return next;
        }),
      );
    },
    [disabled, questions],
  );

  const handleOtherTextChange = useCallback((qIndex: number, text: string): void => {
    setOtherTexts((prev) =>
      prev.map((textValue, textIndex) => (textIndex === qIndex ? text : textValue)),
    );
  }, []);

  const allAnswered = useMemo(
    () =>
      questions.every((_, questionIndex) => {
        const sel = selections[questionIndex];
        if ((sel?.size ?? 0) === 0) return false;
        // If OTHER_KEY is selected but the free-text is empty, not yet answered.
        if (sel?.has(OTHER_KEY) && !otherTexts[questionIndex]?.trim()) return false;
        return true;
      }),
    [questions, selections, otherTexts],
  );

  const handleSubmit = useCallback((): void => {
    if (disabled || !allAnswered) return;
    // Filter options array to preserve display order regardless of click order.
    const answers = questions.map((question, questionIndex) => {
      const sel = selections[questionIndex];
      const normalAnswer = question.options.filter((opt) => sel?.has(opt)).join('、');
      const otherText = sel?.has(OTHER_KEY) ? (otherTexts[questionIndex]?.trim() ?? '') : '';
      return [normalAnswer, otherText].filter(Boolean).join('、');
    });
    const text =
      questions.length === 1
        ? answers[0]
        : questions
            .map((question, questionIndex) => `${question.text}：${answers[questionIndex]}`)
            .join('\n');
    onAnswer(text);
  }, [disabled, allAnswered, questions, selections, otherTexts, onAnswer]);

  return (
    <div className="mt-2 border-t border-gray-200 pt-2">
      {questions.map((question, qIndex) => {
        const otherSelected = selections[qIndex]?.has(OTHER_KEY) ?? false;
        const otherText = otherTexts[qIndex] ?? '';
        return (
          <div key={qIndex} className="mb-3 last:mb-0">
            <div className="mb-1.5 text-xs font-medium text-gray-700">{question.text}</div>
            <div className="flex flex-wrap gap-1.5">
              {question.options.map((option) => {
                if (immediateMode) {
                  return (
                    <button
                      key={option}
                      onClick={(): void => handleImmediate(option)}
                      disabled={disabled}
                      className={optionClasses(false, disabled)}
                    >
                      {option}
                    </button>
                  );
                }
                const isSelected = selections[qIndex]?.has(option) ?? false;
                return (
                  <button
                    key={option}
                    role={question.multiSelect ? 'checkbox' : 'radio'}
                    aria-checked={isSelected}
                    onClick={(): void => handleToggle(qIndex, option, question.multiSelect)}
                    disabled={disabled}
                    className={optionClasses(isSelected, disabled)}
                  >
                    {option}
                  </button>
                );
              })}

              {/* "其他" option — always the last card */}
              {immediateMode ? (
                <button
                  onClick={(): void => {
                    if (!disabled) setImmediateOtherOpen(true);
                  }}
                  disabled={disabled}
                  className={optionClasses(immediateOtherOpen, disabled)}
                >
                  <EditOutlined style={{ fontSize: 11, marginRight: 4 }} />
                  其他
                </button>
              ) : (
                <button
                  role={question.multiSelect ? 'checkbox' : 'radio'}
                  aria-checked={otherSelected}
                  onClick={(): void => handleToggle(qIndex, OTHER_KEY, question.multiSelect)}
                  disabled={disabled}
                  className={optionClasses(otherSelected, disabled)}
                >
                  <EditOutlined style={{ fontSize: 11, marginRight: 4 }} />
                  其他
                </button>
              )}
            </div>

            {/* "其他" free-text input — immediate mode */}
            {immediateMode && immediateOtherOpen && (
              <div className="mt-1.5 flex gap-1.5">
                <input
                  type="text"
                  value={otherText}
                  onChange={(e): void => handleOtherTextChange(0, e.target.value)}
                  onKeyDown={(e): void => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      const trimmed = otherText.trim();
                      if (trimmed && !disabled) handleImmediate(trimmed);
                    }
                  }}
                  placeholder="請輸入..."
                  disabled={disabled}
                  // eslint-disable-next-line jsx-a11y/no-autofocus
                  autoFocus
                  className="flex-1 rounded border border-gray-300 px-2 py-1 text-xs outline-none focus:border-blue-400"
                />
                <button
                  onClick={(): void => {
                    const trimmed = otherText.trim();
                    if (trimmed && !disabled) handleImmediate(trimmed);
                  }}
                  disabled={disabled || !otherText.trim()}
                  className={[
                    'rounded border px-2 py-1 text-xs transition-colors',
                    disabled || !otherText.trim()
                      ? 'cursor-not-allowed border-gray-200 bg-gray-100 text-gray-400 opacity-50'
                      : 'cursor-pointer border-blue-400 bg-blue-500 text-white hover:bg-blue-600',
                  ].join(' ')}
                >
                  送出
                </button>
              </div>
            )}

            {/* "其他" free-text input — form mode */}
            {!immediateMode && otherSelected && (
              <div className="mt-1.5">
                <input
                  type="text"
                  value={otherText}
                  onChange={(e): void => handleOtherTextChange(qIndex, e.target.value)}
                  onKeyDown={(e): void => {
                    // Prevent accidental form submission on Enter inside this input.
                    if (e.key === 'Enter') e.preventDefault();
                  }}
                  placeholder="請輸入..."
                  disabled={disabled}
                  // eslint-disable-next-line jsx-a11y/no-autofocus
                  autoFocus
                  className="w-full rounded border border-gray-300 px-2 py-1 text-xs outline-none focus:border-blue-400"
                />
              </div>
            )}
          </div>
        );
      })}

      {!immediateMode && (
        <button
          disabled={disabled || !allAnswered}
          onClick={handleSubmit}
          className={[
            'mt-1 rounded-lg border px-3 py-1.5 text-xs transition-colors',
            disabled || !allAnswered
              ? 'cursor-not-allowed border-gray-200 bg-gray-100 text-gray-400 opacity-50'
              : 'cursor-pointer border-blue-400 bg-blue-500 text-white hover:bg-blue-600',
          ].join(' ')}
        >
          送出
        </button>
      )}
    </div>
  );
};

export default QuestionCards;
