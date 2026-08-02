import { useCallback, useEffect, useReducer, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { streamAgentMessage, AgentStreamHttpError } from '@/api/agentApi';
import type { AgentEvent, AgentStreamState, StepItem } from '@/types';

// Re-export so consumers can import from one place
export type { AgentStreamState } from '@/types';

type Action =
  | { type: 'START' }
  | { type: 'EVENT'; event: AgentEvent }
  | { type: 'DONE'; durationMs: number }
  | { type: 'STOPPED' }
  | { type: 'NETWORK_ERROR'; error: { code: string; message: string }; durationMs: number }
  | { type: 'ERROR'; error: { code: string; message: string }; durationMs: number }
  | { type: 'RESET' };

const initialState: AgentStreamState = {
  isStreaming: false,
  stopped: false,
  networkError: false,
  steps: [],
  liveText: '',
  answer: null,
  artifact: null,
  error: null,
  thinking: '',
  questions: null,
  codeText: '',
  tables: [],
  durationMs: null,
};

function reducer(state: AgentStreamState, action: Action): AgentStreamState {
  switch (action.type) {
    case 'START':
      return { ...initialState, isStreaming: true };

    case 'STOPPED':
      return { ...state, stopped: true };

    case 'EVENT': {
      const agentEvent = action.event;
      switch (agentEvent.type) {
        case 'STEP': {
          const newStep: StepItem = {
            stepKey: agentEvent.stepKey,
            title: agentEvent.title,
            description: agentEvent.description,
            status: agentEvent.status,
          };
          const idx = state.steps.findIndex(
            (step: StepItem) => step.stepKey === agentEvent.stepKey,
          );
          const steps: StepItem[] =
            idx >= 0
              ? state.steps.map((step: StepItem, stepIndex: number) =>
                  stepIndex === idx ? newStep : step,
                )
              : [...state.steps, newStep];
          return { ...state, steps };
        }
        case 'TOKEN':
          return { ...state, liveText: state.liveText + agentEvent.delta };
        case 'ANSWER':
          return { ...state, answer: agentEvent.text };
        case 'ARTIFACT':
          return {
            ...state,
            artifact: { artifactId: agentEvent.artifactId, title: agentEvent.title },
          };
        case 'ERROR':
          // Keep streaming — backend still emits finalize steps after ERROR; DONE closes it.
          return {
            ...state,
            error: { code: agentEvent.code, message: agentEvent.message },
          };
        case 'THINKING':
          return { ...state, thinking: state.thinking + agentEvent.delta };
        case 'QUESTION':
          return { ...state, questions: agentEvent.questions };
        case 'CODE':
          return { ...state, codeText: state.codeText + agentEvent.delta };
        case 'TABLE':
          return {
            ...state,
            tables: [
              ...state.tables,
              {
                tableId: agentEvent.tableId,
                intent: agentEvent.intent,
                columns: agentEvent.columns,
                rows: agentEvent.rows,
                truncated: agentEvent.truncated,
              },
            ],
          };
        default:
          return state;
      }
    }

    case 'DONE':
      return { ...state, isStreaming: false, durationMs: action.durationMs };

    case 'NETWORK_ERROR':
      return {
        ...state,
        isStreaming: false,
        networkError: true,
        error: action.error,
        durationMs: action.durationMs,
      };

    case 'ERROR':
      return { ...state, isStreaming: false, error: action.error, durationMs: action.durationMs };

    case 'RESET':
      return initialState;

    default:
      return state;
  }
}

export function useAgentStream(sessionId: string): {
  state: AgentStreamState;
  send(question: string, baseArtifactId?: string): Promise<void>;
  stop(): void;
  reset(): void;
} {
  const [state, dispatch] = useReducer(reducer, initialState);
  const queryClient = useQueryClient();
  const controllerRef = useRef<AbortController | null>(null);

  // Abort any in-flight request when the component unmounts
  useEffect(
    () => () => {
      controllerRef.current?.abort();
    },
    [],
  );

  const send = useCallback(
    async (question: string, baseArtifactId?: string): Promise<void> => {
      const startedAt = Date.now();
      dispatch({ type: 'START' });

      const controller = new AbortController();
      controllerRef.current = controller;

      try {
        for await (const event of streamAgentMessage({
          sessionId,
          question,
          baseArtifactId,
          signal: controller.signal,
        })) {
          dispatch({ type: 'EVENT', event });
        }

        // Must await before dispatching DONE, or ChatPanel's edge effect clears the live
        // bubble before refreshed history is in place, causing a flicker.
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['session', sessionId] }),
          queryClient.invalidateQueries({ queryKey: ['sessions'] }),
        ]);
        dispatch({ type: 'DONE', durationMs: Date.now() - startedAt });
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          // Dispatch DONE immediately; two staggered invalidates catch up with the backend's
          // async doOnCancel persistence even under slow DB writes.
          dispatch({ type: 'DONE', durationMs: Date.now() - startedAt });
          setTimeout(() => {
            void Promise.all([
              queryClient.invalidateQueries({ queryKey: ['session', sessionId] }),
              queryClient.invalidateQueries({ queryKey: ['sessions'] }),
            ]);
            setTimeout(() => {
              void Promise.all([
                queryClient.invalidateQueries({ queryKey: ['session', sessionId] }),
                queryClient.invalidateQueries({ queryKey: ['sessions'] }),
              ]);
            }, 800);
          }, 800);
          return;
        }
        if (err instanceof AgentStreamHttpError) {
          dispatch({
            type: 'ERROR',
            error: { code: err.code, message: err.message },
            durationMs: Date.now() - startedAt,
          });
          return;
        }
        // Unexpected network disconnection (not user-initiated, not HTTP error).
        dispatch({
          type: 'NETWORK_ERROR',
          error: { code: 'NETWORK_ERROR', message: '連線中斷，請重新送出一次' },
          durationMs: Date.now() - startedAt,
        });
      }
    },
    [sessionId, queryClient],
  );

  const stop = useCallback((): void => {
    // Mark stopped immediately so the live bubble shows the indicator before AbortError
    // propagates through the async generator.
    dispatch({ type: 'STOPPED' });
    controllerRef.current?.abort();
  }, []);

  const reset = useCallback((): void => {
    dispatch({ type: 'RESET' });
  }, []);

  return { state, send, stop, reset };
}
