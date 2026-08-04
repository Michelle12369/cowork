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

/** 覆寫目前使用者 id。internal 環境的 SSO 接縫用它取代匿名 UUID（見 bootstrap/internal.ts）；
 *  預設環境沒有呼叫端。具名 export 是為了讓耦合顯性化——直接硬寫 localStorage key 的話，
 *  key 改名時 internal 端不會編譯錯誤，只會安靜退回匿名身分。 */
export function setUserId(userId: string): void {
  localStorage.setItem(USER_KEY, userId);
}

export const apiClient = axios.create({ baseURL: '/api' });

apiClient.interceptors.request.use((config) => {
  config.headers['X-User-Id'] = getUserId();
  return config;
});
