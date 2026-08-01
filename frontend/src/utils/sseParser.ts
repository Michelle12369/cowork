import type { AgentEvent } from '@/types';

/** Incremental SSE parser: calls `onEvent` for each valid AgentEvent, separated by blank lines;
 *  `:`-prefixed lines are heartbeats, a parse failure silently discards the block. */
export function createSseParser(onEvent: (event: AgentEvent) => void): {
  feed(chunk: string): void;
  flush(): void;
} {
  let buffer = '';

  function processBlock(block: string): void {
    const dataLines: string[] = [];

    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue; // comment / heartbeat
      if (line.startsWith('data:')) {
        // Strip the "data:" prefix and a single optional leading space
        dataLines.push(line.slice(5).replace(/^ /, ''));
      }
    }

    if (dataLines.length === 0) return;

    const payload = dataLines.join('\n');
    try {
      const event = JSON.parse(payload) as AgentEvent;
      onEvent(event);
    } catch {
      // Silently ignore malformed events
    }
  }

  function drainBuffer(): void {
    let idx: number;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (block.trim()) processBlock(block);
    }
  }

  return {
    feed(chunk: string): void {
      buffer += chunk;
      drainBuffer();
    },

    flush(): void {
      if (buffer.trim()) {
        processBlock(buffer);
        buffer = '';
      }
    },
  };
}
