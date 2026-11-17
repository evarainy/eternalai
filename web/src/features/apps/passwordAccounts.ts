import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { getBindingApiV1CredentialBindingsTargetSystemGet as getBinding } from '../../generated/credential-bindings/credential-bindings';
import type { CredentialBindingView } from '../../generated/credential-bindings/credential-bindings.schemas';
import { useAuthStore } from '../../stores/authStore';
import type { AccountStatus, PasswordTarget } from './systems';

export function passwordBindingQueryKey(generation: number, target: PasswordTarget) {
  return ['credential-binding', generation, target] as const;
}

/** 某个系统的密码绑定状态；软件中心的指示灯和设置卡片读的是同一份。 */
export function usePasswordBinding(target: PasswordTarget): UseQueryResult<CredentialBindingView> {
  const generation = useAuthStore((state) => state.generation);
  return useQuery({
    queryKey: passwordBindingQueryKey(generation, target),
    queryFn: () => getBinding(target),
  });
}

export function passwordStatus(query: UseQueryResult<CredentialBindingView>, target: PasswordTarget): AccountStatus {
  if (query.isPending) return { light: 'busy', text: '正在读取' };
  if (query.isError || query.data === undefined) return { light: 'off', text: '读不到状态' };
  const binding = query.data;
  if (!binding.bound) return { light: 'off', text: '未绑定' };
  if (binding.poll_status === 'invalid') return { light: 'alert', text: '密码已失效' };
  if (binding.poll_status === 'captcha_required') return { light: 'alert', text: '要验证码，已停用' };
  if (binding.poll_status === 'retrying') return { light: 'warn', text: '已绑定，暂时连不上' };
  return { light: 'on', text: target === 'oa' ? '已绑定' : '已保存' };
}
