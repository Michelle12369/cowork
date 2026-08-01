import React from 'react';
import { QUICK_PROMPTS } from '@/config/quickPrompts';

interface Props {
  onPick: (prompt: string) => void;
  disabled?: boolean;
}

const QuickChips: React.FC<Props> = ({ onPick, disabled }) => (
  <div className="mb-2.5 flex flex-wrap gap-1.5">
    {QUICK_PROMPTS.map((chip) => (
      <button
        key={chip.key}
        type="button"
        disabled={disabled}
        onClick={() => onPick(chip.prompt)}
        className={`rounded-full border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-500 ${
          disabled
            ? 'cursor-not-allowed opacity-50'
            : 'cursor-pointer hover:border-blue-400 hover:text-blue-500'
        }`}
      >
        {chip.label}
      </button>
    ))}
  </div>
);

export default QuickChips;
