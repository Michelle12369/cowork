import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { ArrowUpOutlined, PlusOutlined } from '@ant-design/icons';

export interface Props {
  disabled?: boolean;
  /** When true, replaces the send button with a gray stop button. */
  isStreaming?: boolean;
  onSend: (text: string) => void;
  /** Called when the user clicks the stop button (only shown when isStreaming=true). */
  onStop?: () => void;
  onAttach?: () => void;
  /** When set to a non-empty string, fills and focuses the textarea; call onPrefillConsumed
   *  once absorbed so the parent can clear it. */
  prefill?: string;
  onPrefillConsumed?: () => void;
}

/** Small filled-square icon used for the stop-generation button. */
const StopSquareIcon: React.FC = () => (
  <span
    style={{
      display: 'inline-block',
      width: '10px',
      height: '10px',
      backgroundColor: 'currentColor',
      borderRadius: '1px',
      verticalAlign: '-1px',
    }}
  />
);

const PromptSender: React.FC<Props> = ({
  disabled,
  isStreaming,
  onSend,
  onStop,
  onAttach,
  prefill,
  onPrefillConsumed,
}) => {
  const [value, setValue] = useState('');
  // Tracks IME composition (e.g. Chinese/Japanese input). While composing, an
  // Enter keypress commits the candidate text rather than submitting the message.
  const composingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // When a prefill string arrives (e.g. from a quick-chip click), load it into
  // the textarea and focus so the user can edit before sending.
  useEffect(() => {
    if (prefill) {
      setValue(prefill);
      textareaRef.current?.focus();
      onPrefillConsumed?.();
    }
    // onPrefillConsumed is intentionally excluded: it is stable (useCallback with
    // [] deps in ChatPanel) and including it would risk double-firing on re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  const send = useCallback(() => {
    const trimmedText = value.trim();
    if (!trimmedText || disabled) return;
    onSend(trimmedText);
    setValue('');
  }, [value, disabled, onSend]);

  const handleCompositionStart = useCallback((): void => {
    composingRef.current = true;
  }, []);

  const handleCompositionEnd = useCallback((): void => {
    composingRef.current = false;
  }, []);

  return (
    <div className="flex items-end gap-2 rounded-2xl border border-gray-300 bg-white p-2">
      <Button
        type="text"
        size="small"
        aria-label="Attach files"
        icon={<PlusOutlined />}
        disabled={!onAttach}
        onClick={onAttach}
      />
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onCompositionStart={handleCompositionStart}
        onCompositionEnd={handleCompositionEnd}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !composingRef.current) {
            event.preventDefault();
            send();
          }
        }}
        placeholder='Ask eRD AI to analyze your data…  e.g. "build an SPC dashboard for Vt"'
        rows={1}
        className="max-h-24 flex-1 resize-none border-none bg-transparent py-1 text-[13px] leading-relaxed outline-none"
      />
      {isStreaming && onStop ? (
        <Button
          type="primary"
          shape="default"
          className="!bg-gray-500 hover:!bg-gray-600"
          icon={<StopSquareIcon />}
          onClick={onStop}
          aria-label="Stop generation"
        />
      ) : (
        <Button
          type="primary"
          shape="default"
          icon={<ArrowUpOutlined />}
          onClick={send}
          disabled={disabled}
        />
      )}
    </div>
  );
};

export default PromptSender;
