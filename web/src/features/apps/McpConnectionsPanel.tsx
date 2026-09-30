import { useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { authorizeMcp, disconnectMcp, listMcpConnections, listMcpServices, safeMcpLink } from '../../api/mcp';
import type { ConnectionView, ServiceView } from '../../generated/mcp/mcp.schemas';
import { useAuthStore } from '../../stores/authStore';
import styles from './McpPanels.module.css';

const labels: Record<string, string> = {
  ACTIVE: '已连接', AUTHORIZING: '等待授权', PENDING_IDENTITY: '等待确认账号对应关系',
  DISCONNECTED: '已断开', REVOKED: '授权已撤销', EXPIRED: '授权已过期',
};

function validServices(value: unknown): value is ServiceView[] {
  return Array.isArray(value) && value.every((item: unknown) => {
    if (item === null || typeof item !== 'object') return false;
    const entry = item as Record<string, unknown>;
    return typeof entry.service_config_id === 'string' && /^[a-z][a-z0-9_-]{0,63}$/.test(entry.service_config_id)
      && typeof entry.display_name === 'string' && entry.display_name.length > 0
      && typeof entry.enabled === 'boolean';
  }) && new Set(value.map((item) => item.service_config_id)).size === value.length;
}

function validConnections(value: unknown): value is ConnectionView[] {
  return Array.isArray(value) && value.every((item: unknown) => {
    if (item === null || typeof item !== 'object') return false;
    const entry = item as Record<string, unknown>;
    return typeof entry.connection_id === 'string' && entry.connection_id.length > 0
      && typeof entry.service_config_id === 'string' && typeof entry.state === 'string'
      && Object.prototype.hasOwnProperty.call(labels, entry.state)
      && (entry.expires_at === null || typeof entry.expires_at === 'string' && Number.isFinite(Date.parse(entry.expires_at)));
  }) && new Set(value.map((item) => item.service_config_id)).size === value.length;
}

export function McpConnectionsPanel() {
  const generation = useAuthStore((state) => state.generation);
  return <Connections key={generation} generation={generation} />;
}

function Connections({ generation }: { generation: number }) {
  const [services, setServices] = useState<ServiceView[]>([]);
  const [connections, setConnections] = useState<ConnectionView[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [link, setLink] = useState<{ service: string; href: string }>();
  const live = useRef(true);
  const sequence = useRef(0);
  const inFlight = useRef(false);
  const current = (id: number) => live.current && sequence.current === id && useAuthStore.getState().generation === generation;

  useEffect(() => {
    live.current = true;
    const id = ++sequence.current;
    Promise.all([listMcpServices(), listMcpConnections()]).then(([items, links]) => {
      if (!validServices(items) || !validConnections(links)) throw new Error('invalid_connection_view');
      if (live.current && sequence.current === id && useAuthStore.getState().generation === generation) {
        setServices(items); setConnections(links); setLoading(false);
      }
    }).catch(() => {
      if (live.current && sequence.current === id && useAuthStore.getState().generation === generation) {
        setError('暂时读不到连接状态，请稍后重新打开本页。'); setLoading(false);
      }
    });
    return () => { live.current = false; };
  }, [generation]);

  async function act(service: ServiceView, connection?: ConnectionView) {
    if (inFlight.current) return;
    inFlight.current = true;
    const id = ++sequence.current;
    setBusy(true); setError(''); setLink(undefined);
    try {
      if (connection) {
        await disconnectMcp(connection.connection_id);
        if (!current(id)) return;
        setConnections((items) => items.map((item) => item.connection_id === connection.connection_id ? { ...item, state: 'DISCONNECTED' } : item));
      } else {
        const result = await authorizeMcp(service.service_config_id);
        if (!current(id)) return;
        const href = safeMcpLink(result.authorization_url);
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

  return <section className={styles.panel} aria-label="业务服务连接">
    <h2>业务服务连接</h2>
    <p className={styles.muted}>分别连接单位提供的业务服务，授权由业务平台本人确认。</p>
    {loading && <p role="status">正在读取连接状态…</p>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {!loading && !error && services.length === 0 && <p className={styles.muted}>暂未配置业务服务，请等待管理员提供接入配置。</p>}
    {services.map((service) => {
      const connection = connections.find((item) => item.service_config_id === service.service_config_id);
      const connected = connection && !['DISCONNECTED', 'REVOKED', 'EXPIRED'].includes(connection.state);
      return <article className={styles.row} key={service.service_config_id}>
        <strong className={styles.name}>{service.display_name}</strong>
        <span>{connection ? labels[connection.state] ?? '状态待核实' : '尚未连接'}</span>
        <Button disabled={busy || !service.enabled} onClick={() => void act(service)}>{connected ? '重新授权' : '连接账号'}</Button>
        {connected && <Button disabled={busy} onClick={() => void act(service, connection)}>断开连接</Button>}
        {!service.enabled && <span className={styles.muted}>等待管理员完成接入准备</span>}
        {link?.service === service.service_config_id && <a className={styles.link} href={link.href} target="_blank" rel="noopener noreferrer">继续前往授权</a>}
      </article>;
    })}
  </section>;
}
