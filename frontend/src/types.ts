export type StepStatus = 'PENDING' | 'RUNNING' | 'SUCCESS' | 'ERROR';

export interface StepItem {
  stepKey: string;
  title: string;
  description: string | null;
  status: StepStatus;
}

export interface Question {
  text: string;
  options: string[];
  multiSelect: boolean;
}

/** One cell value in a TABLE event's rows — the honest union for what JSON gives us. */
export type TableCellValue = string | number | boolean | null;

/** One query-result table (live-only; see decision 5 — never persisted to history). */
export interface TableResult {
  tableId: string;
  intent: string;
  columns: string[];
  rows: TableCellValue[][];
  truncated: boolean;
}

export type AgentEvent =
  | { type: 'STEP'; stepKey: string; title: string; description: string | null; status: StepStatus }
  | { type: 'TOKEN'; delta: string }
  | { type: 'ANSWER'; text: string }
  | { type: 'ARTIFACT'; artifactId: string; title: string }
  | { type: 'ERROR'; code: string; message: string }
  | { type: 'THINKING'; delta: string }
  | { type: 'QUESTION'; questions: Question[] }
  | { type: 'CODE'; delta: string }
  | {
      type: 'TABLE';
      tableId: string;
      intent: string;
      columns: string[];
      rows: TableCellValue[][];
      truncated: boolean;
    };

export interface AgentStreamState {
  isStreaming: boolean;
  /** True immediately after the user clicks Stop; cleared on next send() or reset(). */
  stopped: boolean;
  /** True when the stream failed due to an unexpected network disconnection (not user cancel). */
  networkError: boolean;
  steps: StepItem[];
  liveText: string;
  answer: string | null;
  artifact: { artifactId: string; title: string } | null;
  error: { code: string; message: string } | null;
  thinking: string;
  questions: Question[] | null;
  codeText: string;
  /** TABLE events accumulated in arrival order (live-only; decision 5 — never persisted). */
  tables: TableResult[];
  /** Elapsed wall-clock ms for the finished turn; null while idle or streaming. */
  durationMs: number | null;
}

export interface SessionSummary {
  id: string;
  title: string;
  updatedAt: string;
}

export interface Message {
  id: string;
  sender: 'USER' | 'AI';
  text: string;
  stepsJson: string | null;
  artifactId: string | null;
  createdAt: string;
  artifactTitle: string | null;
  questionsJson: string | null;
  /** Serialised TableResult[] the answer referenced via a `[[table:id]]` marker, null if none;
   *  persisted so a reloaded history bubble can still render the inline table. */
  referencedTablesJson: string | null;
}

export interface UploadedFileInfo {
  id: string;
  name: string;
  alias: string;
  sizeBytes: number;
  type: string;
  rowCount: number | null;
  expired: boolean;
}

export interface SessionDetail {
  id: string;
  title: string;
  createdAt: string;
  messages: Message[];
  files: UploadedFileInfo[];
}

export interface ArtifactVersion {
  artifactId: string;
  title: string;
  version: number;
}

export interface BrowserJsError {
  message: string;
  line: number;
  col: number;
}
