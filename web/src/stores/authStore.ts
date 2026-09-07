import { create } from 'zustand';

/**
 * 三态，不是两态。
 *
 * 刷新页面时前端**还不知道**自己登不登录着——会话票据在一个 httpOnly cookie 里，JS 读不到。原来的两态
 * 把这一瞬间当成「未登录」，于是每次刷新都先把人踢回登录页。加 `unknown` 就是把「还没问过后端」和
 * 「后端说你没登录」分开：前者既不放行也不重定向，等后端答复。
 *
 * **这里不存任何身份断言。** 没有 localStorage、没有 persist、没有姓名或部门字段。唯一被保存的是浏览器
 * 里那份服务端签名、httpOnly 的 cookie——前端读不到也伪造不了，每次请求由服务端重新验签。恢复登录态因此
 * 是「向后端问一次」，不是「相信上次的记录」。
 */
type AuthenticationStatus = 'unknown' | 'authenticated' | 'unauthenticated';

interface AuthenticationState {
  generation: number;
  status: AuthenticationStatus;
  markAuthenticated: () => void;
  markUnauthenticated: (expectedGeneration?: number) => void;
}

export const useAuthStore = create<AuthenticationState>((set) => ({
  generation: 0,
  status: 'unknown',
  markAuthenticated: () =>
    set((state) => ({
      generation: state.generation + 1,
      status: 'authenticated',
    })),
  markUnauthenticated: (expectedGeneration) =>
    set((state) => {
      if (
        (expectedGeneration !== undefined &&
          expectedGeneration !== state.generation) ||
        state.status === 'unauthenticated'
      ) {
        return state;
      }
      return {
        generation: state.generation + 1,
        status: 'unauthenticated',
      };
    }),
}));

export type { AuthenticationStatus };
