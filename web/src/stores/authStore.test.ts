import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAuthStore } from './authStore';

describe('authentication store', () => {
  beforeEach(() => {
    useAuthStore.setState({ generation: 0, status: 'unknown' });
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('starts out not knowing, rather than assuming either answer', () => {
    // 刷新时票据在一个 httpOnly cookie 里，JS 读不到；假设「已登录」是自称身份，假设「未登录」就是
    // 刷新掉登录态那个 bug。
    expect(useAuthStore.getState().status).toBe('unknown');
  });

  it('resolves the unknown state in either direction', () => {
    useAuthStore.getState().markAuthenticated();
    expect(useAuthStore.getState().status).toBe('authenticated');

    useAuthStore.setState({ generation: 0, status: 'unknown' });
    useAuthStore.getState().markUnauthenticated();
    expect(useAuthStore.getState().status).toBe('unauthenticated');
  });

  it('ignores a stale unauthenticated answer from an earlier session generation', () => {
    useAuthStore.getState().markAuthenticated();
    const staleGeneration = useAuthStore.getState().generation - 1;

    useAuthStore.getState().markUnauthenticated(staleGeneration);

    expect(useAuthStore.getState().status).toBe('authenticated');
  });

  it('never writes an identity assertion to browser storage', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');

    useAuthStore.getState().markAuthenticated();
    useAuthStore.getState().markUnauthenticated();

    /*
     * 唯一被保存的必须是浏览器里那份 httpOnly、服务端签名的 cookie——前端读不到也伪造不了。把
     * `status: 'authenticated'` 写进 localStorage 看似也能修「刷新掉登录态」，但那是在没有任何服务端
     * 确认的情况下自称已登录。
     */
    expect(setItem).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    setItem.mockRestore();
  });

  it('carries no identity fields at all', () => {
    const keys = Object.keys(useAuthStore.getState());

    expect(keys.sort()).toEqual(
      ['generation', 'markAuthenticated', 'markUnauthenticated', 'status'].sort(),
    );
  });
});
