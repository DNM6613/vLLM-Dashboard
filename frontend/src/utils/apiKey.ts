const API_KEY_STORAGE_KEY = 'vllm_api_key';

export function getStoredApiKey(): string {
  try {
    return localStorage.getItem(API_KEY_STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

export function setStoredApiKey(key: string): void {
  try {
    if (key) {
      localStorage.setItem(API_KEY_STORAGE_KEY, key);
    } else {
      localStorage.removeItem(API_KEY_STORAGE_KEY);
    }
  } catch {
  }
}
