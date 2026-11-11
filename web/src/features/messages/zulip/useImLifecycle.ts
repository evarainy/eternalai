/// <reference types="vite/client" />
import { useEffect } from 'react';
import { configurationApiV1ImContextConfigurationGet } from '../../../generated/im-context/im-context';
import { useAuthStore } from '../../../stores/authStore';
import { configureIM, imBridgeBlocked, imLogoutInProgress, registerIMView } from '../hostBridge';
import { disableIm, resetIm, setImRestart, startIm, useImStore } from './imStore';
import { setImUpstreamOrigin } from './links';
import { createHttpTransport } from './transport';
import type { ImTransport } from './transport';

/** 开发期合成数据开关：只在开发构建且 `VITE_IM_DEV_DATA=1` 时动态加载，生产包里这段恒为假。 */
const devTransport = import.meta.env.DEV && import.meta.env.VITE_IM_DEV_DATA === '1'
  ? () => import('./devTransport').then((module) => module.createDevTransport())
  : null;

/**
 * IM 接收器的登录生命周期（FE_PLAN §4.2「接收器挂在哪里」、§5.3）。挂在 AuthenticatedAppShell 上，
 * 切到工作台其他页面也继续接收、更新角标。
 *
 * - 只在已认证、没有退出进行中、身份链路未被冻结，且 IM configuration 非空时启动。
 * - 通过 `registerIMView` 接入现有的冻结入口：`suspendIMIdentity`（退出开始、身份复核失败）立即停止接收、
 *   中止挂起请求、清空 imStore，不等服务端注销完成。
 * - generation 或认证状态变化时同样立即清空，再按新身份重新启动。
 */
export function useImLifecycle(): void {
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);

  useEffect(() => {
    if (status !== 'authenticated' || imLogoutInProgress() || imBridgeBlocked()) return;
    let live = true;
    const isCurrent = () => live && useAuthStore.getState().generation === generation;
    const freeze = () => {
      live = false;
      resetIm();
    };
    // 返回原消息的同页定位入口在 lane 4 接入（FE_PLAN §5.3 最后一条）。
    const unregister = registerIMView(freeze, () => undefined);
    const start = async () => {
      try {
        const config = await configurationApiV1ImContextConfigurationGet();
        if (!isCurrent()) return;
        if (config === null) {
          disableIm();
          return;
        }
        configureIM(config);
        setImUpstreamOrigin(config.im_origin);
        const transport: ImTransport = devTransport ? await devTransport() : createHttpTransport();
        if (isCurrent()) await startIm(transport);
      } catch {
        if (isCurrent()) {
          resetIm();
          useImStore.setState({ phase: 'failed', failure: 'im_unavailable' });
        }
      }
    };
    void start();
    // 「重新连接」从 configuration 开始重走，读 configuration 失败的情形也能恢复。
    setImRestart(() => {
      if (isCurrent()) void start();
    });
    return () => {
      live = false;
      setImRestart(null);
      unregister();
      setImUpstreamOrigin(null);
      resetIm();
    };
  }, [status, generation]);
}
