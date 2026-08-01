import React, { useCallback, useEffect, useRef, useState } from 'react';
import { CodeOutlined, DownOutlined, UpOutlined } from '@ant-design/icons';
import { fetchArtifactRawHtml } from '@/api/artifactApi';

export interface Props {
  /** Row label, e.g. '</> 產生中的 HTML' | '</> HTML' | '</> 查看 HTML'. */
  label: string;
  /** Live data source: when non-empty, rendered directly (no fetch). */
  code?: string | null;
  /** Fetch data source: lazy-loaded on first expand when `code` is absent. */
  artifactId?: string | null;
  /** Auto-scroll the panel to the bottom as `code` grows (live typing). */
  autoScroll?: boolean;
}

const HtmlCodePanel: React.FC<Props> = ({ label, code, artifactId, autoScroll }) => {
  const [expanded, setExpanded] = useState(false); // default collapsed
  const contentRef = useRef<HTMLPreElement>(null);
  // Ref (not state) so it stays out of the effect's deps — avoids a self-abort cycle where
  // setHtmlFetch would mutate deps and trigger cleanup before the effect re-runs.
  const requestedRef = useRef<string | null>(null);
  const [htmlFetch, setHtmlFetch] = useState<{
    artifactId: string;
    status: 'loading' | 'ok' | 'error';
    content?: string;
  } | null>(null);

  const toggle = useCallback(() => setExpanded((prev) => !prev), []);

  const hasLiveCode = !!code;

  // Lazy-fetch on first expand. Deps exclude htmlFetch to avoid a self-aborting cycle;
  // requestedRef guards duplicate fetches.
  useEffect(() => {
    if (!expanded || hasLiveCode || !artifactId) return;
    if (requestedRef.current === artifactId) return;

    requestedRef.current = artifactId;
    setHtmlFetch({ artifactId, status: 'loading' });
    const controller = new AbortController();
    let completed = false;

    fetchArtifactRawHtml(artifactId, controller.signal)
      .then((text) => {
        completed = true;
        setHtmlFetch({ artifactId, status: 'ok', content: text });
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === 'AbortError') return;
        completed = true;
        setHtmlFetch({ artifactId, status: 'error' });
      });

    return () => {
      if (!completed) {
        // Abort only if the request is still in-flight (unmount / artifactId change).
        // Reset the ref so a re-expand after an interrupted collapse can retry.
        controller.abort();
        requestedRef.current = null;
      }
    };
  }, [expanded, hasLiveCode, artifactId]);

  // Auto-scroll to bottom as live code streams in.
  useEffect(() => {
    if (expanded && autoScroll && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [code, expanded, autoScroll]);

  const body = hasLiveCode ? (
    <pre
      ref={contentRef}
      className="mt-1 max-h-80 overflow-auto rounded bg-gray-200 p-2 text-[11px] text-gray-600"
    >
      <code>{code}</code>
    </pre>
  ) : htmlFetch?.artifactId !== artifactId || htmlFetch?.status === 'loading' ? (
    <div className="text-[11px] text-gray-400">載入中…</div>
  ) : htmlFetch?.status === 'error' ? (
    <div className="text-[11px] text-red-400">此版本無原始碼可檢視（無法載入）</div>
  ) : (
    <pre
      ref={contentRef}
      className="mt-1 max-h-80 overflow-auto rounded bg-gray-200 p-2 text-[11px]"
    >
      <code>{htmlFetch?.content}</code>
    </pre>
  );

  return (
    <div className="mt-2">
      <button
        onClick={toggle}
        className="flex w-full items-center gap-1 text-left text-[11px] text-gray-400 hover:text-gray-600"
      >
        <CodeOutlined style={{ fontSize: 11 }} />
        <span className="flex-1">{label}</span>
        {expanded ? (
          <UpOutlined style={{ fontSize: 10 }} />
        ) : (
          <DownOutlined style={{ fontSize: 10 }} />
        )}
      </button>
      {expanded && <div className="mt-1">{body}</div>}
    </div>
  );
};

export default HtmlCodePanel;
