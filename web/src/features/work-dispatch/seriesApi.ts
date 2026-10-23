import { requestWorkDispatch } from '../../api/workDispatchTransport';
import type { AttachmentView } from './AttachmentsPanel';
import type { DispatchRequest } from './dispatchApi';

export type SeriesTemplate = Omit<DispatchRequest, 'due_at'>;
export interface SeriesRequest {
  weekdays: number[];
  local_time: string;
  timezone: string;
  start_date: string;
  deadline_spec: { days_after: number; local_time: string };
  template: SeriesTemplate;
  enabled: boolean;
}
export interface SeriesView extends SeriesRequest {
  series_id: string;
  version: number;
  next_due_at: string | null;
  created_at: string;
  updated_at: string;
  attachments: AttachmentView[];
}
export interface SeriesPage<T> { items: T[]; next_cursor: string | null }
export interface SeriesHistoryItem {
  version: number;
  operation: 'create' | 'update' | 'enable' | 'disable';
  occurred_at: string;
  snapshot: SeriesView;
}
export interface SeriesOccurrence {
  occurrence_id: string;
  scheduled_at: string;
  series_version: number;
  state: 'generated' | 'blocked';
  blocked_code: string | null;
  generated_at: string | null;
  items: { work_object_id: string; title: string; status: string; due_at: string | null }[];
  visible_count: number;
}
export interface SeriesMutation { series: SeriesView; replayed: boolean }
const base = '/api/v1/work-object-series';
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
export async function listSeries(cursor?: string) {
  return (await requestWorkDispatch<SeriesPage<SeriesView>>({ url: base, method: 'GET', params: { ...(cursor ? { cursor } : {}), limit: 50 } })).data;
}
export function getSeries(id: string) {
  return requestWorkDispatch<SeriesView>({ url: path(id), method: 'GET' });
}
export function saveSeries(request: SeriesRequest, key: string, existing?: { id: string; etag: string }) {
  return requestWorkDispatch<SeriesMutation>({ url: existing ? path(existing.id) : base, method: existing ? 'PUT' : 'POST',
    headers: { 'Idempotency-Key': key, ...(existing ? { 'If-Match': existing.etag } : {}) }, data: request });
}
export function setSeriesEnabled(id: string, enabled: boolean, etag: string, key: string) {
  return requestWorkDispatch<SeriesMutation>({ url: `${path(id)}/${enabled ? 'enable' : 'disable'}`, method: 'POST',
    headers: { 'Idempotency-Key': key, 'If-Match': etag } });
}
export async function seriesHistory(id: string, cursor?: string) {
  return (await requestWorkDispatch<SeriesPage<SeriesHistoryItem>>({ url: `${path(id)}/history`, method: 'GET', params: { ...(cursor ? { cursor } : {}), limit: 50 } })).data;
}
export async function seriesOccurrences(id: string, cursor?: string) {
  return (await requestWorkDispatch<SeriesPage<SeriesOccurrence>>({ url: `${path(id)}/occurrences`, method: 'GET', params: { ...(cursor ? { cursor } : {}), limit: 50 } })).data;
}
