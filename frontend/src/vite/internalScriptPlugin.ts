import type { Plugin } from 'vite';

/** 公司環境把內部 library 以 global script 掛進 index.html。URL 未設時不注入任何標籤，
 *  使家裡產出的 HTML 與加這個 plugin 之前逐字元相同。 */
export function internalScriptPlugin(scriptUrl: string | undefined): Plugin {
  const trimmedUrl = scriptUrl?.trim();
  return {
    name: 'internal-script',
    transformIndexHtml: () =>
      trimmedUrl ? [{ tag: 'script', attrs: { src: trimmedUrl }, injectTo: 'head' as const }] : [],
  };
}
