import { ApiError } from '../../api/mutator';
import {
  cancelIMDelivery, confirmIMDelivery, getIMDelivery, listIMDeliveries, listIMDeliveryRecipients,
  prepareIMDelivery, reconcileIMDelivery, verifyIMDelivery,
} from '../../generated/im-deliveries/im-deliveries';
import type {
  DeliveryPrepare, DeliveryVerification, DeliveryView,
} from '../../generated/im-deliveries/im-deliveries.schemas';

export type { DeliveryView, DeliveryRecipient } from '../../generated/im-deliveries/im-deliveries.schemas';
export type DeliveryState = DeliveryView['state'];
export type VerificationResult = DeliveryVerification['result'];
export type PrepareDelivery = DeliveryPrepare;
export type VerifyDelivery = DeliveryVerification;

export const readDeliveryRecipients = listIMDeliveryRecipients;
export const readDeliveries = listIMDeliveries;
export const readDelivery = getIMDelivery;
export const prepareDelivery = prepareIMDelivery;
export const confirmDelivery = (view: DeliveryView) => confirmIMDelivery(view.delivery_id, { confirmation_sha256: view.confirmation_sha256 });
export const cancelDelivery = cancelIMDelivery;
export const reconcileDelivery = reconcileIMDelivery;
export const verifyDelivery = verifyIMDelivery;

export const deliveryFileDownloadUrl = (artifactId: string) =>
  `/api/v1/file-artifacts/${encodeURIComponent(artifactId)}/content`;

export const DELIVERY_STATE_LABELS: Record<DeliveryState, string> = {
  prepared: '等待本人确认', ready: '已受理，等待发送', sending: '发送结果待核实',
  accepted: 'IM 已接受', known_rejected: 'IM 未接受', unknown: '发送结果待核实',
  cancelled: '已取消，未开始发送', expired: '已过期，未开始发送',
};

const RESULT_REASONS: Record<string, string> = {
  im_delivery_target_changed: '发送目标或连接配置与预览不一致，未发送，请重新预览。',
  im_bot_identity_changed: '助手账号与预览不一致，未发送，请联系维护人员核对。',
  im_bot_identity_unavailable: '无法核验助手账号，未发送，请联系维护人员核对。',
  im_delivery_rejected: 'IM 明确拒绝了本次发送，请核对收件人与助手权限。',
  im_delivery_mapping_changed: '本人或收件人的 IM 身份映射已变化，未发送，请重新预览并确认。',
  im_delivery_response_unknown: '发送响应未能确认，消息可能已发送，请核对原结果。',
  im_delivery_unconfirmed: '尚无充分证据确认原消息是否被 IM 接受，请继续核对原结果。',
  im_delivery_reconciled: '已在原私聊中核得唯一且内容一致的原消息。',
};

export function deliveryResultReason(code: string | null): string | null {
  return code === null ? null : RESULT_REASONS[code] ?? null;
}

const PREVIEW_ERRORS: Record<string, string> = {
  im_delivery_expiry_invalid: '请核对期限：最迟发送须在未来，且不晚于文件授权截止；授权截止不能晚于文件到期。',
  im_delivery_recipient_invalid: '请选择另一位同事作为收件人。',
  im_delivery_mapping_invalid: '收件人的 IM 身份配置无效，请联系维护人员核对。',
  im_delivery_mapping_unavailable: '当前身份没有可用的 IM 映射，请联系维护人员核对。',
  im_delivery_recipient_unavailable: '该收件人目前不可用，请重新选择或联系维护人员核对。',
  file_artifact_not_found: '该文件不存在或当前无权使用，请重新选择。',
  file_artifact_expired: '文件已过期，请重新上传。',
  file_artifact_unavailable: '文件服务暂时不可用，请稍后再试。',
};

export function deliveryError(error: unknown): string {
  if (error instanceof ApiError) return error.status === 401 ? '登录已失效，请重新登录。' : PREVIEW_ERRORS[error.code] ?? error.message;
  return '连接中断，结果尚未确认。请检查原记录。';
}

export const deliveryTime = (value: string) => new Date(value).toLocaleString('zh-CN');
