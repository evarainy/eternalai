export {
  servicesApiV1McpServicesGet as listMcpServices,
  connectionsApiV1McpConnectionsGet as listMcpConnections,
  authorizeApiV1McpConnectionsServiceConfigIdAuthorizePost as authorizeMcp,
  disconnectApiV1McpConnectionsConnectionIdDisconnectPost as disconnectMcp,
  operationApiV1McpOperationsOperationIdGet as getMcpOperation,
  operationsApiV1McpOperationsGet as listMcpOperations,
  resumeApiV1McpOperationsOperationIdResumePost as resumeMcpOperation,
} from '../generated/mcp/mcp';

export function safeMcpLink(value: string | null | undefined): string | undefined {
  if (!value || [...value].some((char) => char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127)) return undefined;
  try {
    const url = new URL(value);
    if (url.username || url.password || url.hash) return undefined;
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(url.hostname))) return undefined;
    return url.href;
  } catch { return undefined; }
}
