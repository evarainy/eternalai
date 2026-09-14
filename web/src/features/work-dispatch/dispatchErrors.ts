import { ApiError } from '../../api/mutator';

type Category = 'reject' | 'directory' | 'snapshot' | 'stop' | 'uncertain' | 'read';
export interface DispatchFailure { text: string; category: Category }
const shared: Record<string, DispatchFailure> = {
  '401:authentication_required': { text: '登录状态已失效；请重新登录，登录后不会自动发布。', category: 'reject' },
  '403:directory_membership_missing': { text: '未找到您的部门归属；请联系管理员核对目录。', category: 'reject' },
  '403:directory_membership_ambiguous': { text: '您有多个部门归属；请联系管理员核实。', category: 'reject' },
  '403:not_department_head': { text: '当前账号没有任务派发权限；请联系管理员核对。', category: 'reject' },
  '403:cross_department_dispatch_denied': { text: '所选对象不在可派发范围；请重新读取候选并核对。', category: 'reject' },
  '404:dispatch_target_not_found': { text: '部分对象已无法确认；请返回部门首页重载核对。', category: 'reject' },
  '503:organization_directory_missing': { text: '目录尚未完成首次同步；暂不能选人或发布，请联系维护人员。', category: 'directory' },
  '503:organization_directory_stale': { text: '目录已过期；请等待同步恢复后重载并核对。', category: 'directory' },
  '503:organization_directory_unavailable': { text: '部门和人员目录暂时不可用；请主动重试读取目录。', category: 'directory' },
  '503:work_object_unavailable': { text: '工作事项服务尚未配置；请保留正文并联系维护人员。', category: 'uncertain' },
};
const getErrors: Record<string, DispatchFailure> = {
  ...shared,
  '403:directory_scope_denied': { text: '当前身份暂不能使用人员目录；请联系管理员。', category: 'read' },
  '409:organization_directory_snapshot_changed': { text: '目录已更新，请重新确认所有交办对象。', category: 'snapshot' },
  '422:dispatch_options_request_invalid': { text: '目录查询信息有误；请重载首页或联系维护人员。', category: 'read' },
};
const postErrors: Record<string, DispatchFailure> = {
  ...shared,
  '403:csrf_validation_failed': { text: '页面校验未通过；请刷新后到工作事项核对。', category: 'stop' },
  '403:dispatch_target_membership_ambiguous': { text: '所选人员有多个归属，本次整批未获准；请核实后人工调整。', category: 'reject' },
  '409:idempotency_key_reused': { text: '提交标识与内容不一致；请停止提交，核对原结果并联系维护人员。', category: 'stop' },
  '422:idempotency_key_invalid': { text: '提交标识有误；请保留内容并联系维护人员。', category: 'stop' },
  '422:dispatch_request_invalid': { text: '交办信息有误；请核对标题、对象、时间与提醒。', category: 'reject' },
  '503:work_object_audit_unavailable': { text: '提交记录暂无法确认；请在本页面主动重试原请求。', category: 'uncertain' },
  '503:work_object_dispatch_failed': { text: '结果待确认；请在本页面主动重试原请求。', category: 'uncertain' },
};

export function dispatchFailure(method: 'GET' | 'POST', error: unknown): DispatchFailure {
  const match = error instanceof ApiError ? (method === 'GET' ? getErrors : postErrors)[`${error.status}:${error.code}`] : undefined;
  return match ?? (method === 'GET'
    ? { text: '目录读取失败；请重载目录或联系维护人员。', category: 'read' }
    : { text: '结果待确认；请主动重试原请求，或到工作事项核对。', category: 'uncertain' });
}
