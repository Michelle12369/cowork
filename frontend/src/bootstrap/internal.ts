export interface InternalBootstrap {
  initialize: () => Promise<void>;
}

// 公司初始化接縫：internal.impl.ts 只存在於公司環境。import.meta.glob 對不存在的檔案
// 回傳空物件而非 build error——這是本接縫在家裡能成立的原因。
const impls = import.meta.glob<InternalBootstrap>('./internal.impl.ts');

/** 公司環境的啟動初始化（例如 SSO 決定 X-User-Id）；家裡無實作檔時為 no-op。
 *  loaders 參數僅供測試注入，正式路徑一律走上面的 glob 結果。 */
export async function initInternalRuntime(
  loaders: Record<string, () => Promise<InternalBootstrap>> = impls,
): Promise<void> {
  const load = loaders['./internal.impl.ts'];
  if (!load) return;
  await (await load()).initialize();
}
