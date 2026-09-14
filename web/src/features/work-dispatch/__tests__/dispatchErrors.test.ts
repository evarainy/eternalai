import { expect, it } from 'vitest';
import { ApiError } from '../../../api/mutator';
import { dispatchFailure } from '../dispatchErrors';

const common: Array<[number, string, string, string]> = [
  [401, 'authentication_required', '登录状态已失效', 'reject'],
  [403, 'directory_membership_missing', '未找到您的部门归属', 'reject'],
  [403, 'directory_membership_ambiguous', '您有多个部门归属', 'reject'],
  [403, 'not_department_head', '当前账号没有任务派发权限', 'reject'],
  [403, 'cross_department_dispatch_denied', '所选对象不在可派发范围', 'reject'],
  [404, 'dispatch_target_not_found', '部分对象已无法确认', 'reject'],
  [503, 'organization_directory_missing', '目录尚未完成首次同步', 'directory'],
  [503, 'organization_directory_stale', '目录已过期', 'directory'],
  [503, 'organization_directory_unavailable', '部门和人员目录暂时不可用', 'directory'],
  [503, 'work_object_unavailable', '工作事项服务尚未配置', 'uncertain'],
];
const getOnly: typeof common = [
  [403, 'directory_scope_denied', '当前身份暂不能使用人员目录', 'read'],
  [409, 'organization_directory_snapshot_changed', '目录已更新', 'snapshot'],
  [422, 'dispatch_options_request_invalid', '目录查询信息有误', 'read'],
];
const postOnly: typeof common = [
  [403, 'csrf_validation_failed', '页面校验未通过', 'stop'],
  [403, 'dispatch_target_membership_ambiguous', '所选人员有多个归属，本次整批未获准', 'reject'],
  [409, 'idempotency_key_reused', '提交标识与内容不一致', 'stop'],
  [422, 'idempotency_key_invalid', '提交标识有误', 'stop'],
  [422, 'dispatch_request_invalid', '交办信息有误', 'reject'],
  [503, 'work_object_audit_unavailable', '提交记录暂无法确认', 'uncertain'],
  [503, 'work_object_dispatch_failed', '结果待确认', 'uncertain'],
];
for (const method of ['GET', 'POST'] as const) {
  const cases = [...common, ...(method === 'GET' ? getOnly : postOnly)];
  it(`E1 exact ${method} code set cardinality`, () => {
    expect(new Set(cases.map(([status, code]) => `${status}:${code}`)).size).toBe(method === 'GET' ? 13 : 17);
  });
  it.each(cases)(`E1 ${method} maps exact status/code %s %s`, (status, code, text, category) => {
    const actual = dispatchFailure(method, new ApiError(status, code, 'untrusted-message'));
    expect(actual.category).toBe(category);
    expect(actual.text.startsWith(text)).toBe(true);
    expect(actual.text).not.toContain('untrusted-message');
    expect(actual.text).toMatch(/请|核对|重试/);
    expect(dispatchFailure(method, new ApiError(418, code, text))).toEqual(dispatchFailure(method, new Error('offline')));
  });
  it.each(method === 'GET' ? postOnly : getOnly)(`E1 ${method} does not consume the other method code %s %s`, (status, code) => {
    expect(dispatchFailure(method, new ApiError(status, code, ''))).toEqual(dispatchFailure(method, new Error('offline')));
  });
}
it('E1 unknown errors distinguish directory reads from uncertain writes', () => {
  expect(dispatchFailure('GET', new Error('private'))).toEqual({ category: 'read', text: '目录读取失败；请重载目录或联系维护人员。' });
  expect(dispatchFailure('POST', new Error('private'))).toEqual({ category: 'uncertain', text: '结果待确认；请主动重试原请求，或到工作事项核对。' });
});
