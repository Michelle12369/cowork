import { describe, expect, it } from 'vitest';
import { internalScriptPlugin } from './internalScriptPlugin';

function runTransform(scriptUrls: string | undefined): unknown {
  const transform = internalScriptPlugin(scriptUrls).transformIndexHtml;
  if (typeof transform !== 'function') throw new Error('transformIndexHtml 必須是函式');
  // hook 型別宣告了 this 參數，但實作忽略它；轉型成無 this 的函式型別，測試不需要
  // 建構真的 plugin context。
  const callTransform = transform as (html: string, ctx: unknown) => unknown;
  return callTransform('<html></html>', {
    path: '/index.html',
    filename: 'index.html',
  } as never);
}

describe('internalScriptPlugin', () => {
  it('internalScriptPlugin_urlUnset_injectsNothing', () => {
    expect(runTransform(undefined)).toEqual([]);
  });

  it('internalScriptPlugin_urlBlank_injectsNothing', () => {
    expect(runTransform('   ')).toEqual([]);
  });

  it('internalScriptPlugin_singleUrl_injectsOneScript', () => {
    expect(runTransform('https://internal.example/sso.js')).toEqual([
      { tag: 'script', attrs: { src: 'https://internal.example/sso.js' }, injectTo: 'head' },
    ]);
  });

  it('internalScriptPlugin_threeCommaSeparatedUrls_injectsAllThreeInInputOrder', () => {
    const result = runTransform(
      'https://internal.example/a.js,https://internal.example/b.js,https://internal.example/c.js',
    );

    expect(result).toEqual([
      { tag: 'script', attrs: { src: 'https://internal.example/a.js' }, injectTo: 'head' },
      { tag: 'script', attrs: { src: 'https://internal.example/b.js' }, injectTo: 'head' },
      { tag: 'script', attrs: { src: 'https://internal.example/c.js' }, injectTo: 'head' },
    ]);
  });

  it('internalScriptPlugin_blankAndEmptyEntries_trimsAndSkipsEmpty', () => {
    const result = runTransform(
      '  https://internal.example/a.js , , https://internal.example/b.js  ,   ',
    );

    expect(result).toEqual([
      { tag: 'script', attrs: { src: 'https://internal.example/a.js' }, injectTo: 'head' },
      { tag: 'script', attrs: { src: 'https://internal.example/b.js' }, injectTo: 'head' },
    ]);
  });
});
