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

/** 覆寫目前使用者 id（internal SSO 接縫用它取代匿名 UUID，見 bootstrap/internal.ts）。具名
 *  export 讓耦合顯性化——硬寫 localStorage key 的話，key 改名 internal 端只會安靜退回匿名身分。 */
export function setUserId(userId: string): void {
  localStorage.setItem(USER_KEY, userId);
}

export const apiClient = axios.create({ baseURL: '/api' });

apiClient.interceptors.request.use((config) => {
  config.headers['X-User-Id'] = getUserId();
  return config;
});
