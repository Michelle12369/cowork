import React, { useMemo } from 'react';
import { useSuspenseQuery } from '@tanstack/react-query';
import { fetchArtifactHtml } from '@/api/artifactApi';
import { injectCspMeta } from '@/utils/artifactCsp';

interface ArtifactFrameProps {
  artifactId: string;
  /** 遞增即重新抓取並重掛 iframe（repair reload 與手動 refresh 共用）。 */
  reloadNonce: number;
  title: string;
  /** 供父層做 postMessage 來源比對（runtime error 回報）。 */
  iframeRef?: React.RefObject<HTMLIFrameElement>;
}

/** 經認證通道抓 artifact HTML 再以 srcdoc 呈現——iframe src 導覽帶不了 auth header。
 *  sandbox 維持 allow-scripts（opaque origin）；CSP 以 meta 注入（srcdoc 不吃 response header）。
 *  呼叫端 MUST 以 SuspenseLoader + ErrorBoundary 包覆。 */
const ArtifactFrame: React.FC<ArtifactFrameProps> = ({
  artifactId,
  reloadNonce,
  title,
  iframeRef,
}) => {
  const { data: rawHtml } = useSuspenseQuery({
    queryKey: ['artifact-html', artifactId, reloadNonce],
    queryFn: () => fetchArtifactHtml(artifactId, reloadNonce),
    staleTime: Infinity,
  });

  const secureHtml = useMemo(() => injectCspMeta(rawHtml, window.location.origin), [rawHtml]);

  return (
    <iframe
      ref={iframeRef}
      key={`${artifactId}-${reloadNonce}`}
      srcDoc={secureHtml}
      sandbox="allow-scripts"
      className="absolute inset-0 h-full w-full border-0"
      title={title}
    />
  );
};

export default ArtifactFrame;
