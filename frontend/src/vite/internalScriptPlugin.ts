import type { Plugin } from 'vite';

/** internal 環境把內部 library 以 global script 掛進 index.html。逗號分隔多支 URL，
 *  MUST 保持輸入順序注入——三支 library 之間有載入順序相依。未設或全空時不注入任何
 *  標籤，使預設環境產出的 HTML 與加這個 plugin 之前逐字元相同。 */
export function internalScriptPlugin(scriptUrls: string | undefined): Plugin {
  const trimmedUrls = (scriptUrls ?? '')
    .split(',')
    .map((url) => url.trim())
    .filter((url) => url.length > 0);
  return {
    name: 'internal-script',
    transformIndexHtml: () =>
      trimmedUrls.map((url) => ({
        tag: 'script' as const,
        attrs: { src: url },
        injectTo: 'head' as const,
      })),
  };
}
