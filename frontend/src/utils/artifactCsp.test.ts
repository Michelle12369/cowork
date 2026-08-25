import { describe, expect, test } from 'vitest';
import { injectCspMeta } from './artifactCsp';

const ORIGIN = 'http://localhost:3000';

describe('injectCspMeta', () => {
  test('inserts meta right after <head>', () => {
    const html = '<!doctype html><html><head><title>t</title></head><body></body></html>';
    const result = injectCspMeta(html, ORIGIN);
    const headIndex = result.indexOf('<head>');
    const metaIndex = result.indexOf('<meta http-equiv="Content-Security-Policy"');
    expect(metaIndex).toBeGreaterThan(headIndex);
    expect(metaIndex).toBeLessThan(result.indexOf('<title>'));
  });

  test('policy uses origin host-source, never self keyword', () => {
    const result = injectCspMeta('<head></head>', ORIGIN);
    expect(result).toContain(`script-src ${ORIGIN} 'unsafe-inline'`);
    expect(result).toContain("connect-src 'none'");
    expect(result).toContain("default-src 'none'");
    expect(result).not.toContain("'self'");
  });

  test('handles <head> with attributes', () => {
    const result = injectCspMeta('<head lang="en"><script></script></head>', ORIGIN);
    expect(result.indexOf('Content-Security-Policy')).toBeLessThan(result.indexOf('<script>'));
  });

  test('prepends meta when no head tag exists', () => {
    const result = injectCspMeta('<div>bare</div>', ORIGIN);
    expect(result.startsWith('<meta http-equiv="Content-Security-Policy"')).toBe(true);
    expect(result).toContain('<div>bare</div>');
  });

  test('regression: <header> tag is never mistaken for <head>', () => {
    // <head[^>]*> 曾誤配 <header>，把 CSP meta 插進 BODY——瀏覽器不吃 body 內的 meta CSP，
    // connect-src 'none' 形同虛設。無 <head> 時必須走 prepend 分支，插在 <header> 之前。
    const html = '<header></header><script>x</script>';
    const result = injectCspMeta(html, ORIGIN);
    expect(result.indexOf('<meta http-equiv="Content-Security-Policy"')).toBe(0);
    expect(result.indexOf('<meta')).toBeLessThan(result.indexOf('<header>'));
  });
});
