/** Artifact srcdoc 的 CSP 以 <meta> 注入：srcdoc 文件不吃 response header CSP，
 *  且 opaque origin 下 'self' 匹配不到任何來源，host 必須用父頁 origin 明寫。 */

const HEAD_OPEN_TAG = /<head\b[^>]*>/i;

function buildPolicy(origin: string): string {
  return [
    "default-src 'none'",
    `script-src ${origin} 'unsafe-inline'`,
    "style-src 'unsafe-inline'",
    `img-src ${origin} data:`,
    "connect-src 'none'",
  ].join('; ');
}

export function injectCspMeta(html: string, origin: string): string {
  const metaTag = `<meta http-equiv="Content-Security-Policy" content="${buildPolicy(origin)}">`;
  const headMatch = HEAD_OPEN_TAG.exec(html);
  if (!headMatch) {
    return metaTag + html;
  }
  const insertAt = headMatch.index + headMatch[0].length;
  return html.slice(0, insertAt) + metaTag + html.slice(insertAt);
}
