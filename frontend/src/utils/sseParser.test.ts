import { vi } from 'vitest';
import { createSseParser } from './sseParser';
import type { AgentEvent } from '@/types';

function tokenEvent(delta: string): AgentEvent {
  return { type: 'TOKEN', delta };
}

describe('createSseParser', () => {
  it('single chunk containing multiple events', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    const chunk =
      'data: {"type":"TOKEN","delta":"hello"}\n\n' + 'data: {"type":"TOKEN","delta":" world"}\n\n';

    parser.feed(chunk);

    expect(events).toHaveLength(2);
    expect(events[0]).toEqual(tokenEvent('hello'));
    expect(events[1]).toEqual(tokenEvent(' world'));
  });

  it('event spanning two chunks (data line cut mid-stream)', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    // First chunk ends partway through the JSON value
    parser.feed('data: {"type":"TOKEN","del');
    expect(events).toHaveLength(0);

    // Second chunk completes the event
    parser.feed('ta":"hello"}\n\n');
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual(tokenEvent('hello'));
  });

  it('ignores SSE comment lines (heartbeat :ka)', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    // A heartbeat comment followed by a real event
    const chunk = ':ka\n\n' + 'data: {"type":"TOKEN","delta":"hi"}\n\n';

    parser.feed(chunk);

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual(tokenEvent('hi'));
  });

  it('ignores events whose data is not valid JSON', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    const chunk = 'data: {not valid json}\n\n' + 'data: {"type":"TOKEN","delta":"ok"}\n\n';

    parser.feed(chunk);

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual(tokenEvent('ok'));
  });

  it('joins multiple data: lines with \\n before JSON.parse', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    // JSON split across two data: lines — whitespace between tokens is valid JSON
    const chunk = 'data: {"type":"TOKEN",\ndata: "delta":"hello"}\n\n';

    parser.feed(chunk);

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual(tokenEvent('hello'));
  });

  it('flush() processes data remaining in the buffer without trailing \\n\\n', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    // Feed without the final blank line
    parser.feed('data: {"type":"TOKEN","delta":"last"}\n');
    expect(events).toHaveLength(0);

    parser.flush();
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual(tokenEvent('last'));
  });

  it('handles STEP, ANSWER, ARTIFACT, ERROR event types correctly', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    const chunk =
      'data: {"type":"STEP","stepKey":"s1","title":"T","description":null,"status":"RUNNING"}\n\n' +
      'data: {"type":"ANSWER","text":"done"}\n\n' +
      'data: {"type":"ARTIFACT","artifactId":"a1","title":"Chart"}\n\n' +
      'data: {"type":"ERROR","code":"E01","message":"oops"}\n\n';

    parser.feed(chunk);

    expect(events).toHaveLength(4);
    expect(events[0]).toEqual({
      type: 'STEP',
      stepKey: 's1',
      title: 'T',
      description: null,
      status: 'RUNNING',
    });
    expect(events[1]).toEqual({ type: 'ANSWER', text: 'done' });
    expect(events[2]).toEqual({ type: 'ARTIFACT', artifactId: 'a1', title: 'Chart' });
    expect(events[3]).toEqual({ type: 'ERROR', code: 'E01', message: 'oops' });
  });

  it('does not emit anything for an event block with only comment lines', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    parser.feed(':keep-alive\n\n:another-comment\n\n');

    expect(events).toHaveLength(0);
  });

  it('onEvent is not called for a block with no data: lines', () => {
    const onEvent = vi.fn();
    const parser = createSseParser(onEvent);

    parser.feed('event: ping\n\n');

    expect(onEvent).not.toHaveBeenCalled();
  });

  it('parses THINKING event and passes it through to onEvent', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    parser.feed('data: {"type":"THINKING","delta":"Let me think"}\n\n');

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ type: 'THINKING', delta: 'Let me think' });
  });

  it('parses QUESTION event and passes it through to onEvent', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    const questions = [
      { text: 'Which chart?', options: ['Bar', 'Line'], multiSelect: false },
      { text: 'Pick columns', options: ['a', 'b'], multiSelect: true },
    ];
    parser.feed(`data: ${JSON.stringify({ type: 'QUESTION', questions })}\n\n`);

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ type: 'QUESTION', questions });
  });

  it('parses TABLE event and passes it through to onEvent', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    const tablePayload = {
      type: 'TABLE',
      tableId: 'tbl_1',
      intent: '計算各機台的不良率',
      columns: ['machine_id', 'defect_rate'],
      rows: [['M1', 0.02]],
      truncated: false,
    };
    parser.feed(`data: ${JSON.stringify(tablePayload)}\n\n`);

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual(tablePayload);
  });

  it('ignores unknown event types without throwing', () => {
    const events: AgentEvent[] = [];
    const parser = createSseParser((event) => events.push(event));

    // An event whose type is not in the union — parser does not crash
    const chunk =
      'data: {"type":"UNKNOWN_FUTURE_TYPE","payload":"x"}\n\n' +
      'data: {"type":"TOKEN","delta":"after"}\n\n';

    parser.feed(chunk);

    // The unknown type is forwarded as-is (JSON parsed); only the TOKEN is a known union member
    // The key behaviour: no exception is thrown and subsequent events still arrive.
    expect(events).toHaveLength(2);
    expect(events[1]).toEqual({ type: 'TOKEN', delta: 'after' });
  });
});
