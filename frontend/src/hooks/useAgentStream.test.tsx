import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { vi } from 'vitest';
import React from 'react';
import { useAgentStream } from './useAgentStream';

vi.mock('@/api/apiClient', () => ({
  getAuthHeaders: vi.fn().mockReturnValue({ 'X-User-Id': 'test-user-id' }),
  // apiClient (axios instance) is not used by useAgentStream — omit here
}));

/** Build a ReadableStream from pre-encoded SSE string chunks. */
function makeStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

/** React wrapper that provides a QueryClient. */
function makeWrapper(qc: QueryClient): React.FC<{ children: React.ReactNode }> {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function freshClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

/** Stub window.fetch for one test; cleaned up in beforeEach. */
function stubFetch(response: object): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
}

describe('useAgentStream', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('initial state is idle / empty', () => {
    const qc = freshClient();
    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    expect(result.current.state).toEqual({
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
      startedAt: null,
    });
  });

  it('accumulates TOKEN events into liveText; isStreaming=false when done', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream([
        'data: {"type":"TOKEN","delta":"hello"}\n\n',
        'data: {"type":"TOKEN","delta":" world"}\n\n',
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('question');
    });

    expect(result.current.state.liveText).toBe('hello world');
    expect(result.current.state.isStreaming).toBe(false);
  });

  it('upserts STEP events by stepKey', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream([
        // First arrival: RUNNING
        'data: {"type":"STEP","stepKey":"k1","title":"T1","description":null,"status":"RUNNING"}\n\n',
        // Upsert: same key → update status to SUCCESS
        'data: {"type":"STEP","stepKey":"k1","title":"T1","description":null,"status":"SUCCESS"}\n\n',
        // New key
        'data: {"type":"STEP","stepKey":"k2","title":"T2","description":"desc","status":"PENDING"}\n\n',
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.steps).toHaveLength(2);
    expect(result.current.state.steps[0]).toEqual({
      stepKey: 'k1',
      title: 'T1',
      description: null,
      status: 'SUCCESS',
    });
    expect(result.current.state.steps[1]).toEqual({
      stepKey: 'k2',
      title: 'T2',
      description: 'desc',
      status: 'PENDING',
    });
  });

  it('records ANSWER text', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"ANSWER","text":"All done."}\n\n']),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.answer).toBe('All done.');
  });

  it('records ARTIFACT event', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"ARTIFACT","artifactId":"art-1","title":"My Chart"}\n\n']),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.artifact).toEqual({
      artifactId: 'art-1',
      title: 'My Chart',
    });
    expect(result.current.state.isStreaming).toBe(false);
  });

  it('calls invalidateQueries for session and sessions on completion', async () => {
    const qc = freshClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"TOKEN","delta":"x"}\n\n']),
    });

    const { result } = renderHook(() => useAgentStream('sess-42'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['session', 'sess-42'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] });
  });

  it('sets error state on a non-2xx response and does not stream', async () => {
    const qc = freshClient();
    stubFetch({
      ok: false,
      status: 400,
      json: vi.fn().mockResolvedValue({ message: 'Bad request', code: 'INVALID_INPUT' }),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.error).toEqual({
      code: 'INVALID_INPUT',
      message: 'Bad request',
    });
    expect(result.current.state.isStreaming).toBe(false);
  });

  it('falls back to status code when non-2xx body has no code/message', async () => {
    const qc = freshClient();
    stubFetch({
      ok: false,
      status: 503,
      json: vi.fn().mockRejectedValue(new Error('not json')),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.error).toMatchObject({ code: '503' });
    expect(result.current.state.isStreaming).toBe(false);
  });

  it('sets error state on an ERROR event from the stream', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream([
        'data: {"type":"ERROR","code":"E42","message":"something went wrong"}\n\n',
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.error).toEqual({
      code: 'E42',
      message: 'something went wrong',
    });
    expect(result.current.state.isStreaming).toBe(false);
  });

  it('ERROR event keeps streaming until done; trailing STEP is still applied', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream([
        'data: {"type":"ERROR","code":"E1","message":"boom"}\n\n',
        // Backend finalize step arrives AFTER the ERROR event
        'data: {"type":"STEP","stepKey":"s4","title":"Rendering","description":null,"status":"ERROR"}\n\n',
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    // error preserved, the post-error STEP was applied, and the stream ended via DONE
    expect(result.current.state.error).toEqual({ code: 'E1', message: 'boom' });
    expect(result.current.state.steps).toEqual([
      { stepKey: 's4', title: 'Rendering', description: null, status: 'ERROR' },
    ]);
    expect(result.current.state.isStreaming).toBe(false);
  });

  it('invalidates history even when the stream carried an ERROR event', async () => {
    const qc = freshClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"ERROR","code":"E1","message":"boom"}\n\n']),
    });

    const { result } = renderHook(() => useAgentStream('sess-err'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['session', 'sess-err'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] });
  });

  it('stays streaming until invalidateQueries resolves, then dispatches DONE', async () => {
    const qc = freshClient();
    let resolveInvalidate!: () => void;
    const gate = new Promise<void>((resolve) => {
      resolveInvalidate = resolve;
    });
    vi.spyOn(qc, 'invalidateQueries').mockReturnValue(gate as Promise<void>);

    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"TOKEN","delta":"x"}\n\n']),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.send('q');
      // Flush the stream read; send() is now parked on the pending invalidate gate.
      await new Promise((r) => setTimeout(r, 0));
    });

    // Stream fully read but DONE not yet dispatched — invalidate has not resolved.
    expect(result.current.state.liveText).toBe('x');
    expect(result.current.state.isStreaming).toBe(true);

    await act(async () => {
      resolveInvalidate();
      await sendPromise;
    });

    expect(result.current.state.isStreaming).toBe(false);
  });

  it('reset() clears all state back to initial', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"TOKEN","delta":"text"}\n\n']),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });
    expect(result.current.state.liveText).toBe('text');

    act(() => {
      result.current.reset();
    });

    expect(result.current.state).toEqual({
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
      startedAt: null,
    });
  });

  it('send() sends correct headers including X-User-Id', async () => {
    const qc = freshClient();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: makeStream([]),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useAgentStream('sess-99'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('my question');
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/sess-99/messages',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          'X-User-Id': 'test-user-id',
        }),
        body: JSON.stringify({ question: 'my question' }),
      }),
    );
  });

  it('send() omits baseArtifactId from body when not provided', async () => {
    const qc = freshClient();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: makeStream([]),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useAgentStream('sess-99'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('my question');
    });

    const calledBody = JSON.parse(
      (fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string,
    ) as Record<string, unknown>;
    expect(calledBody).toEqual({ question: 'my question' });
    expect(calledBody).not.toHaveProperty('baseArtifactId');
  });

  it('send() includes baseArtifactId in body when provided', async () => {
    const qc = freshClient();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: makeStream([]),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useAgentStream('sess-99'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('my question', 'art-42');
    });

    const calledBody = JSON.parse(
      (fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string,
    ) as Record<string, unknown>;
    expect(calledBody).toEqual({ question: 'my question', baseArtifactId: 'art-42' });
  });

  it('send() omits selectedGroups from body when not provided or empty', async () => {
    const qc = freshClient();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: makeStream([]),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useAgentStream('sess-99'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('my question', undefined, []);
    });

    const calledBody = JSON.parse(
      (fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string,
    ) as Record<string, unknown>;
    expect(calledBody).toEqual({ question: 'my question' });
    expect(calledBody).not.toHaveProperty('selectedGroups');
  });

  it('send() includes selectedGroups in body when provided', async () => {
    const qc = freshClient();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: makeStream([]),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useAgentStream('sess-99'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('my question', 'art-42', ['mes', 'erp']);
    });

    const calledBody = JSON.parse(
      (fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string,
    ) as Record<string, unknown>;
    expect(calledBody).toEqual({
      question: 'my question',
      baseArtifactId: 'art-42',
      selectedGroups: ['mes', 'erp'],
    });
  });

  it('accumulates THINKING deltas into thinking state', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream([
        'data: {"type":"THINKING","delta":"Let me"}\n\n',
        'data: {"type":"THINKING","delta":" think..."}\n\n',
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.thinking).toBe('Let me think...');
  });

  it('sets questions state from QUESTION event', async () => {
    const qc = freshClient();
    const questionsPayload = [
      { text: 'Which chart type?', options: ['Bar', 'Line', 'Pie'], multiSelect: false },
      { text: 'Select columns', options: ['col1', 'col2'], multiSelect: true },
    ];
    stubFetch({
      ok: true,
      body: makeStream([
        `data: ${JSON.stringify({ type: 'QUESTION', questions: questionsPayload })}\n\n`,
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.questions).toEqual(questionsPayload);
  });

  // ── stop() ────────────────────────────────────────────────────────────────

  it('stop() is exposed from the hook', () => {
    const qc = freshClient();
    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });
    expect(typeof result.current.stop).toBe('function');
  });

  it('AbortError path: isStreaming becomes false, accumulated state preserved; invalidate fires after 800ms delay', async () => {
    vi.useFakeTimers();
    try {
      const qc = freshClient();
      const invalidateSpy = vi.spyOn(qc, 'invalidateQueries').mockResolvedValue(undefined);

      // Stream yields one token then simulates AbortError on the next read.
      const encoder = new TextEncoder();
      let readCount = 0;
      const abortStream = {
        getReader: () => ({
          read: vi.fn().mockImplementation(async () => {
            readCount++;
            if (readCount === 1) {
              return {
                value: encoder.encode('data: {"type":"TOKEN","delta":"partial"}\n\n'),
                done: false,
              };
            }
            const err = Object.assign(new Error('The user aborted a request.'), {
              name: 'AbortError',
            });
            throw err;
          }),
          cancel: vi.fn().mockResolvedValue(undefined),
          releaseLock: vi.fn(),
        }),
      };

      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body: abortStream }));

      const { result } = renderHook(() => useAgentStream('s1'), {
        wrapper: makeWrapper(qc),
      });

      await act(async () => {
        await result.current.send('q');
      });

      // Accumulated partial text preserved; DONE dispatched immediately.
      expect(result.current.state.liveText).toBe('partial');
      expect(result.current.state.isStreaming).toBe(false);
      // Invalidate must NOT have fired yet — it is delayed ~800ms.
      expect(invalidateSpy).not.toHaveBeenCalled();

      // First stage: advance 800ms → first invalidate batch fires.
      await act(async () => {
        vi.advanceTimersByTime(800);
      });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['session', 's1'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] });
    } finally {
      vi.useRealTimers();
    }
  });

  it('AbortError path: two-stage invalidate fires at 800ms then 1600ms total', async () => {
    vi.useFakeTimers();
    try {
      const qc = freshClient();
      const invalidateSpy = vi.spyOn(qc, 'invalidateQueries').mockResolvedValue(undefined);

      const encoder = new TextEncoder();
      let readCount = 0;
      const abortStream = {
        getReader: () => ({
          read: vi.fn().mockImplementation(async () => {
            readCount++;
            if (readCount === 1) {
              return {
                value: encoder.encode('data: {"type":"TOKEN","delta":"x"}\n\n'),
                done: false,
              };
            }
            const err = Object.assign(new Error('The user aborted a request.'), {
              name: 'AbortError',
            });
            throw err;
          }),
          cancel: vi.fn().mockResolvedValue(undefined),
          releaseLock: vi.fn(),
        }),
      };

      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body: abortStream }));

      const { result } = renderHook(() => useAgentStream('s1'), {
        wrapper: makeWrapper(qc),
      });

      await act(async () => {
        await result.current.send('q');
      });

      expect(invalidateSpy).not.toHaveBeenCalled();

      // First stage at 800ms
      await act(async () => {
        vi.advanceTimersByTime(800);
      });
      expect(invalidateSpy).toHaveBeenCalledTimes(2); // session + sessions

      // Second stage at 800ms after the first (1600ms total)
      await act(async () => {
        vi.advanceTimersByTime(800);
      });
      expect(invalidateSpy).toHaveBeenCalledTimes(4); // second batch: session + sessions
    } finally {
      vi.useRealTimers();
    }
  });

  it('stop() immediately sets stopped=true before the AbortError propagates', () => {
    const qc = freshClient();

    // Fetch hangs indefinitely so the stream never errors — we only need to observe
    // the synchronous STOPPED dispatch that happens before abort propagates.
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    act(() => {
      void result.current.send('q');
    });

    expect(result.current.state.isStreaming).toBe(true);
    expect(result.current.state.stopped).toBe(false);

    act(() => {
      result.current.stop();
    });

    // stopped becomes true synchronously; isStreaming is still true at this point
    // (the AbortError hasn't propagated yet because fetch is hanging).
    expect(result.current.state.stopped).toBe(true);
    expect(result.current.state.isStreaming).toBe(true);
  });

  it('stop() triggers AbortController.abort on the active request', () => {
    const qc = freshClient();
    const abortMock = vi.fn();
    const OrigAbortController = globalThis.AbortController;

    // Patch AbortController so we can observe abort() calls.
    vi.stubGlobal(
      'AbortController',
      class {
        abort = abortMock;
        signal = new OrigAbortController().signal;
      },
    );

    // fetch hangs indefinitely (never resolves) — we just need the controller set.
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    act(() => {
      void result.current.send('q');
    });

    act(() => {
      result.current.stop();
    });

    expect(abortMock).toHaveBeenCalledTimes(1);
  });

  it('reset() clears thinking and questions back to initial values', async () => {
    const qc = freshClient();
    const questionsPayload = [{ text: 'Pick one', options: ['A', 'B'], multiSelect: false }];
    stubFetch({
      ok: true,
      body: makeStream([
        'data: {"type":"THINKING","delta":"some thought"}\n\n',
        `data: ${JSON.stringify({ type: 'QUESTION', questions: questionsPayload })}\n\n`,
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.thinking).toBe('some thought');
    expect(result.current.state.questions).toEqual(questionsPayload);

    act(() => {
      result.current.reset();
    });

    expect(result.current.state.thinking).toBe('');
    expect(result.current.state.questions).toBeNull();
  });

  // ── Network error (non-AbortError, non-HTTP) ──────────────────────────────

  it('unexpected network error sets networkError=true and connection error message', async () => {
    const qc = freshClient();
    // Simulate a raw network failure (fetch itself rejects before returning a response).
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.networkError).toBe(true);
    expect(result.current.state.isStreaming).toBe(false);
    expect(result.current.state.error).toEqual({
      code: 'NETWORK_ERROR',
      message: '連線中斷，請重新送出一次',
    });
  });

  it('reset() clears stopped and networkError back to false', async () => {
    const qc = freshClient();
    // Trigger network error so both error flags are set.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.networkError).toBe(true);

    act(() => {
      result.current.reset();
    });

    expect(result.current.state.networkError).toBe(false);
    expect(result.current.state.stopped).toBe(false);
  });

  // ── CODE events ───────────────────────────────────────────────────────────

  it('accumulates CODE deltas into codeText', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream([
        'data: {"type":"CODE","delta":"<div>"}\n\n',
        'data: {"type":"CODE","delta":"x</div>"}\n\n',
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.codeText).toBe('<div>x</div>');
    // liveText must remain unaffected by CODE events
    expect(result.current.state.liveText).toBe('');
  });

  it('reset() clears codeText, stopped, and networkError back to empty/false', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"CODE","delta":"<html>"}\n\n']),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.codeText).toBe('<html>');

    act(() => {
      result.current.reset();
    });

    expect(result.current.state.codeText).toBe('');
    expect(result.current.state.stopped).toBe(false);
    expect(result.current.state.networkError).toBe(false);
  });

  it('a new send() (START) clears previous codeText', async () => {
    const qc = freshClient();
    // First send — streams a CODE event
    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"CODE","delta":"<body>"}\n\n']),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('first');
    });

    expect(result.current.state.codeText).toBe('<body>');

    // Second send — no CODE events this time
    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"TOKEN","delta":"just text"}\n\n']),
    });

    await act(async () => {
      await result.current.send('second');
    });

    expect(result.current.state.codeText).toBe('');
  });

  // ── TABLE events ──────────────────────────────────────────────────────────

  it('accumulates TABLE events into tables, in arrival order', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream([
        `data: ${JSON.stringify({
          type: 'TABLE',
          tableId: 'tbl_1',
          intent: '計算各機台的不良率',
          columns: ['machine_id', 'defect_rate'],
          rows: [['M1', 0.02]],
          truncated: false,
        })}\n\n`,
        `data: ${JSON.stringify({
          type: 'TABLE',
          tableId: 'tbl_2',
          intent: '找出離群值',
          columns: ['lot_id', 'value'],
          rows: [['L1', 5.5]],
          truncated: true,
        })}\n\n`,
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.tables).toEqual([
      {
        tableId: 'tbl_1',
        intent: '計算各機台的不良率',
        columns: ['machine_id', 'defect_rate'],
        rows: [['M1', 0.02]],
        truncated: false,
      },
      {
        tableId: 'tbl_2',
        intent: '找出離群值',
        columns: ['lot_id', 'value'],
        rows: [['L1', 5.5]],
        truncated: true,
      },
    ]);
  });

  it('reset() clears tables back to an empty array', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream([
        `data: ${JSON.stringify({
          type: 'TABLE',
          tableId: 'tbl_1',
          intent: 'intent',
          columns: ['a'],
          rows: [[1]],
          truncated: false,
        })}\n\n`,
      ]),
    });

    const { result } = renderHook(() => useAgentStream('s1'), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.send('q');
    });

    expect(result.current.state.tables).toHaveLength(1);

    act(() => {
      result.current.reset();
    });

    expect(result.current.state.tables).toEqual([]);
  });

  // ── durationMs ────────────────────────────────────────────────────────────

  it('records durationMs when the stream completes', async () => {
    const qc = freshClient();
    stubFetch({
      ok: true,
      body: makeStream(['data: {"type":"ANSWER","text":"done"}\n\n']),
    });

    const { result } = renderHook(() => useAgentStream('s1'), { wrapper: makeWrapper(qc) });

    await act(async () => {
      await result.current.send('question');
    });

    expect(result.current.state.durationMs).toBeGreaterThanOrEqual(0);
  });

  it('records durationMs when the stream fails with a network error', async () => {
    const qc = freshClient();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')));

    const { result } = renderHook(() => useAgentStream('s1'), { wrapper: makeWrapper(qc) });

    await act(async () => {
      await result.current.send('question');
    });

    expect(result.current.state.networkError).toBe(true);
    expect(result.current.state.durationMs).toBeGreaterThanOrEqual(0);
  });
});
