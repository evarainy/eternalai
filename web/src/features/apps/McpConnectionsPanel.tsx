import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { authorizeMcp, disconnectMcp, safeMcpAuthorizationLink } from '../../api/mcp';
import type { ConnectionView, ServiceView } from '../../generated/mcp/mcp.schemas';
import { MCP_CONNECTIONS_ANCHOR } from '../../contracts/runtimeProjection';
import { Icon } from '../../shared/ui/Icon';
import { useAuthStore } from '../../stores/authStore';
import {
  CONNECTION_LABELS, connectionHeld, connectionLight, serviceAccountsQueryKey, useServiceAccounts,
} from './serviceAccounts';
import type { ServiceAccounts } from './serviceAccounts';
import styles from './Systems.module.css';

/**
 * 业务服务连接：单位提供的每个业务服务一行，一盏灯、一句状态、连接和断开两个按钮。
 *
 * 授权在业务平台本人确认：点「连接账号」只拿到一条授权链接，要你再点一次才会离开本页。
 * 管理员还没配置业务服务时，按钮照样摆在这里，只是点不动，旁边写明原因。
 */
export function McpConnectionsPanel() {
  const generation = useAuthStore((state) => state.generation);
  return <Connections key={generation} generation={generation} />;
}

function Connections({ generation }: { generation: number }) {
  const queryClient = useQueryClient();
  const query = useServiceAccounts();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [link, setLink] = useState<{ service: string; href: string }>();
  const live = useRef(true);
  const sequence = useRef(0);
  const inFlight = useRef(false);
  const section = useRef<HTMLElement>(null);
  const current = (id: number) => live.current && sequence.current === id && useAuthStore.getState().generation === generation;
  const loading = query.isPending;

  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  // 从对话里的「去授权」跳过来时带着锚点；这一段读完数据才有内容，读完再滚过去。
  useEffect(() => {
    if (!loading && window.location.hash === `#${MCP_CONNECTIONS_ANCHOR}`) {
      section.current?.scrollIntoView?.({ block: 'start' });
    }
  }, [loading]);

  async function act(service: ServiceView, connection?: ConnectionView) {
    if (inFlight.current) return;
    inFlight.current = true;
    const id = ++sequence.current;
    setBusy(true); setError(''); setLink(undefined);
    try {
      if (connection) {
        await disconnectMcp(connection.connection_id);
        if (!current(id)) return;
        queryClient.setQueryData<ServiceAccounts>(serviceAccountsQueryKey(generation), (accounts) => accounts && {
          ...accounts,
          connections: accounts.connections.map((item) => (
            item.connection_id === connection.connection_id ? { ...item, state: 'DISCONNECTED' } : item
          )),
        });
      } else {
        const result = await authorizeMcp(service.service_config_id);
        if (!current(id)) return;
        const href = safeMcpAuthorizationLink(result.authorization_url);
        if (!href) throw new Error('unavailable');
        setLink({ service: service.service_config_id, href });
      }
    } catch {
      if (current(id)) setError('操作未确认完成，请刷新连接状态后再处理。');
    } finally {
      inFlight.current = false;
      if (current(id)) setBusy(false);
    }
  }

  // 重读失败时旧数据还留在缓存里：读不准就不摆按钮，不拿旧状态让人连接或断开。
  const accounts = query.isError ? undefined : query.data;
  const services = accounts?.services ?? [];
  const connections = accounts?.connections ?? [];

  return (
    <section aria-label="业务服务连接" className={styles.services} id={MCP_CONNECTIONS_ANCHOR} ref={section}>
      {loading ? <p className={styles.bindingFact} role="status">正在读取连接状态…</p> : null}
      {query.isError ? (
        <p className={styles.bindingNote} data-tone="error" role="alert">暂时读不到连接状态，请稍后重新打开本页。</p>
      ) : null}
      {error ? <p className={styles.bindingNote} data-tone="error" role="alert">{error}</p> : null}
      {accounts !== undefined && services.length === 0 ? (
        <article className={styles.service}>
          <span aria-hidden="true" className={styles.light} data-state="none" />
          <div className={styles.serviceText}>
            <b>业务服务</b>
            <small>暂未配置业务服务，请等待管理员提供接入配置。</small>
          </div>
          <div className={styles.serviceActions}>
            <button className="wb-btn wb-btn-primary wb-btn-sm" disabled type="button">连接账号</button>
          </div>
        </article>
      ) : null}
      {services.map((service) => {
        const connection = connections.find((item) => item.service_config_id === service.service_config_id);
        const held = connectionHeld(connection);
        return (
          <article className={styles.service} key={service.service_config_id}>
            <span aria-hidden="true" className={styles.light} data-state={connectionLight(connection)} />
            <div className={styles.serviceText}>
              <b>{service.display_name}</b>
              <small>
                {connection ? CONNECTION_LABELS[connection.state] ?? '状态待核实' : '尚未连接'}
                {service.enabled ? null : ' · 等待管理员完成接入准备'}
              </small>
            </div>
            <div className={styles.serviceActions}>
              <button
                className="wb-btn wb-btn-primary wb-btn-sm"
                disabled={busy || !service.enabled}
                onClick={() => { void act(service); }}
                type="button"
              >
                {held ? '重新授权' : '连接账号'}
              </button>
              {held ? (
                <button
                  className="wb-btn wb-btn-sm"
                  disabled={busy}
                  onClick={() => { void act(service, connection); }}
                  type="button"
                >
                  断开连接
                </button>
              ) : null}
            </div>
            {link?.service === service.service_config_id ? (
              <a className={styles.serviceLink} href={link.href} rel="noopener noreferrer" target="_blank">
                继续前往授权
                <Icon name="external" size={15} />
              </a>
            ) : null}
          </article>
        );
      })}
    </section>
  );
}
