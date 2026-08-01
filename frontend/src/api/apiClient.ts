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

export const apiClient = axios.create({ baseURL: '/api' });

apiClient.interceptors.request.use((config) => {
  config.headers['X-User-Id'] = getUserId();
  return config;
});
