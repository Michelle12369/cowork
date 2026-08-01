export interface QuickPrompt {
  key: string;
  label: string;
  prompt: string;
}

// 修改快速指令只需動這個檔案
export const QUICK_PROMPTS: QuickPrompt[] = [
  { key: 'spc', label: 'SPC analysis', prompt: 'Run an SPC analysis on Vt (gate CD).' },
  { key: 'pareto', label: 'Defect pareto', prompt: 'Build a defect pareto from the CSV.' },
  {
    key: 'trend',
    label: 'Trend report',
    prompt: 'Show a yield trend report for the last 24 lots.',
  },
];
