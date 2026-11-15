import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { listMcpConnections, listMcpServices } from '../../api/mcp';
import type { ConnectionView, ServiceView } from '../../generated/mcp/mcp.schemas';
import { useAuthStore } from '../../stores/authStore';
import type { AccountStatus, LightState } from './systems';

/** 单位提供的业务服务，和我在每个服务上的连接。指示灯和连接操作读的是同一份。 */
export interface ServiceAccounts {
  services: ServiceView[];
  connections: ConnectionView[];
}

export const CONNECTION_LABELS: Record<string, string> = {
  ACTIVE: '已连接',
  AUTHORIZING: '等待授权',
  PENDING_IDENTITY: '等待确认账号对应关系',
  DISCONNECTED: '已断开',
  REVOKED: '授权已撤销',
  EXPIRED: '授权已过期',
};

const ENDED_STATES = ['DISCONNECTED', 'REVOKED', 'EXPIRED'];

/** 这条连接还在（已连接或正在授权），不是断开、撤销、过期。 */
export function connectionHeld(connection: ConnectionView | undefined): connection is ConnectionView {
  return connection !== undefined && !ENDED_STATES.includes(connection.state);
}

export function connectionLight(connection: ConnectionView | undefined): LightState {
  if (!connectionHeld(connection)) return 'off';
  return connection.state === 'ACTIVE' ? 'on' : 'warn';
}

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
      && Object.prototype.hasOwnProperty.call(CONNECTION_LABELS, entry.state)
      && (entry.expires_at === null || typeof entry.expires_at === 'string' && Number.isFinite(Date.parse(entry.expires_at)));
  }) && new Set(value.map((item) => item.service_config_id)).size === value.length;
}

export function serviceAccountsQueryKey(generation: number) {
  return ['service-accounts', generation] as const;
}

/**
 * 读业务服务和连接。接口返回的形状不对就当读不到，不拿半截数据去判断「没连上」。
 * 去业务平台授权是在另一个窗口里做的，回到本窗口时重读一次，灯才跟得上。
 */
export function useServiceAccounts(): UseQueryResult<ServiceAccounts> {
  const generation = useAuthStore((state) => state.generation);
  return useQuery({
    queryKey: serviceAccountsQueryKey(generation),
    queryFn: async (): Promise<ServiceAccounts> => {
      const [services, connections] = await Promise.all([listMcpServices(), listMcpConnections()]);
      if (!validServices(services) || !validConnections(connections)) throw new Error('invalid_connection_view');
      return { services, connections };
    },
    retry: false,
    refetchOnWindowFocus: 'always',
  });
}

export function serviceStatus(query: UseQueryResult<ServiceAccounts>): AccountStatus {
  if (query.isPending) return { light: 'busy', text: '正在读取' };
  if (query.isError || query.data === undefined) return { light: 'off', text: '读不到状态' };
  const { services, connections } = query.data;
  if (services.length === 0) return { light: 'none', text: '管理员还没有配置' };
  const held = services
    .map((service) => connections.find((item) => item.service_config_id === service.service_config_id))
    .filter(connectionHeld);
  if (held.some((connection) => connection.state === 'ACTIVE')) return { light: 'on', text: '已连接' };
  if (held.length > 0) return { light: 'warn', text: '等待授权' };
  return { light: 'off', text: '未连接' };
}
