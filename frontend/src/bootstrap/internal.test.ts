import { describe, expect, it, vi } from 'vitest';
import { initInternalRuntime } from './internal';

describe('initInternalRuntime', () => {
  it('initInternalRuntime_noImplFile_resolvesWithoutThrowing', async () => {
    // 家裡的真實狀態：internal.impl.ts 不存在 → glob 回傳空物件。
    await expect(initInternalRuntime()).resolves.toBeUndefined();
  });

  it('initInternalRuntime_implPresent_callsInitialize', async () => {
    const initialize = vi.fn().mockResolvedValue(undefined);
    await initInternalRuntime({ './internal.impl.ts': async () => ({ initialize }) });
    expect(initialize).toHaveBeenCalledOnce();
  });

  it('initInternalRuntime_implThrows_propagates', async () => {
    const initialize = vi.fn().mockRejectedValue(new Error('SSO 未載入'));
    await expect(
      initInternalRuntime({ './internal.impl.ts': async () => ({ initialize }) }),
    ).rejects.toThrow('SSO 未載入');
  });
});
