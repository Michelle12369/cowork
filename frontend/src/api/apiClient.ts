import axios from 'axios';

const USER_KEY = 'erd_user_id';

export function getUserId(): string {
  let id = localStorage.getItem(USER_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(USER_KEY, id);
  }
  return id;
}

/** 回傳這次請求要送出的 auth header；回傳值語意是「完全取代」，預設以外的 provider
 *  不回傳 X-User-Id 就不會送。internal 環境用 Keycloak header 取代匿名 UUID，見
 *  bootstrap/internal.ts。 */
export type AuthHeaderProvider = () => Record<string, string>;

let authHeaderProvider: AuthHeaderProvider = () => ({ 'X-User-Id': getUserId() });

/** 覆寫 auth header provider（internal SSO 接縫用它換上 Keycloak header）。provider
 *  MUST 每次請求都被呼叫，NEVER 快取回傳值——internal 的 token 會在背景刷新，快取住
 *  會在過期後開始 401。 */
export function setAuthHeaderProvider(next: AuthHeaderProvider): void {
  authHeaderProvider = next;
}

/** 取得目前應送出的 auth header；axios interceptor 與 agentApi 的 raw fetch 共用同一個
 *  provider，兩條路徑保持一致。 */
export function getAuthHeaders(): Record<string, string> {
  return authHeaderProvider();
}

export const apiClient = axios.create({ baseURL: '/api' });

apiClient.interceptors.request.use((config) => {
  const headers = getAuthHeaders();
  for (const [headerName, headerValue] of Object.entries(headers)) {
    config.headers[headerName] = headerValue;
  }
  return config;
});
