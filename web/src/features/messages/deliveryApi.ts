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

export const DELIVERY_STATE_LABELS: Record<DeliveryState, string> = {
  prepared: '等待本人确认', ready: '已受理，等待发送', sending: '发送结果待核实',
  accepted: 'IM 已接受', known_rejected: 'IM 未接受', unknown: '发送结果待核实',
  cancelled: '已取消，未开始发送', expired: '已过期，未开始发送',
};

export function deliveryError(error: unknown): string {
  if (error instanceof ApiError) return error.status === 401 ? '登录已失效，请重新登录。' : error.message;
  return '连接中断，结果尚未确认。请检查原记录。';
}

export const deliveryTime = (value: string) => new Date(value).toLocaleString('zh-CN');
