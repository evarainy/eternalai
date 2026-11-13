import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listWorkObjectsApiV1WorkObjectsGet as listWorkObjects } from '../../generated/work-objects/work-objects';
import type {
  OAWorkObjectView,
  WorkObjectListResponseItemsItem,
} from '../../generated/work-objects/work-objects.schemas';
import { useAuthStore } from '../../stores/authStore';
import { readWorkQueue } from '../work-dispatch/workObjectLifecycleApi';
import type { HandlingRelationView } from '../work-dispatch/workObjectLifecycleApi';

/**
 * 案头卡片的数据。每张卡片各取各的：一个来源不通只影响用它的那几张，别的照常。
 * 先显示上次的数据再刷新（同一个 key 的缓存一分钟内直接复用）。
 */
export type WorkItem = WorkObjectListResponseItemsItem;

const STALE_TIME_MS = 60_000;

function useDeskQuery<T>(name: string, queryFn: () => Promise<T>) {
  const generation = useAuthStore((state) => state.generation);
  return useQuery({
    queryKey: ['desk', generation, name] as const,
    queryFn,
    staleTime: STALE_TIME_MS,
  });
}

/** 没办完的事：OA 待办和内部工作事项。 */
export function useOpenWork() {
  return useDeskQuery('open-work', () => listWorkObjects({ oa_view: 'active', completion: 'active' }));
}

/** 近 30 天办结的内部事项（OA 已办结数据还没有接入）。 */
export function useCompletedWork() {
  return useDeskQuery('completed-work', () => listWorkObjects({ oa_view: 'active', completion: 'completed' }));
}

/** 我交办出去、还在办的事。 */
export function useSentWork() {
  return useDeskQuery('sent-work', () => readWorkQueue({ queue: 'mine_sent', completion: 'active' }));
}

/**
 * 案头的「现在」：每分钟走一格。今天 / 明天的分组和「已超期」都按它推导，
 * 页面一直开着跨过午夜或某件事的截止时间后，卡片会自己更新。
 */
export function useDeskNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let timer: number;
    const schedule = () => {
      timer = window.setTimeout(() => {
        setNow(new Date());
        schedule();
      }, 60_000 - (Date.now() % 60_000));
    };
    schedule();
    return () => window.clearTimeout(timer);
  }, []);
  return now;
}

export function workItemTitle(item: WorkItem): string {
  return (item.state_authority === 'internal' ? item.title : item.source_title) ?? '未命名事项';
}

function startOfDay(now: Date, dayOffset: number): number {
  const day = new Date(now);
  day.setDate(day.getDate() + dayOffset);
  day.setHours(0, 0, 0, 0);
  return day.getTime();
}

function dueTime(item: WorkItem): number | null {
  if (item.due_at === null) return null;
  const parsed = Date.parse(item.due_at);
  return Number.isNaN(parsed) ? null : parsed;
}

const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
const monthDay = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' });

export function formatClock(timestamp: number): string {
  return clock.format(timestamp);
}

export interface DueRow {
  id: string;
  /** 这一行左边的时间：今天、明天写钟点，已超期写日期。 */
  when: string;
  overdue: boolean;
  title: string;
  /** 来源与要我做什么。 */
  note: string;
}

export interface DueGroups {
  overdue: DueRow[];
  today: DueRow[];
  tomorrow: DueRow[];
}

const HANDLING_ACTION_LABELS: Record<OAWorkObjectView['handling_action'], string> = {
  ai_draft: '让 AI 先写',
  self_serve: '我自己办',
  go_source_system: '去 OA 办',
  view_only: '先看看',
};

function sourceLabel(item: WorkItem): string {
  return item.state_authority === 'internal' ? '任务交办' : 'OA 办公';
}

/**
 * 已超期（截止时间已过，含今天已经过点的）、今天还没到点的、明天到期的，各自按时间从早到晚。
 * 没有截止时间的事不在这张卡片里。
 */
export function groupByDue(items: readonly WorkItem[], now: Date): DueGroups {
  const todayStart = startOfDay(now, 0);
  const tomorrowStart = startOfDay(now, 1);
  const afterTomorrow = startOfDay(now, 2);
  const groups: DueGroups = { overdue: [], today: [], tomorrow: [] };
  const dated = items
    .map((item) => ({ item, due: dueTime(item) }))
    .filter((entry): entry is { item: WorkItem; due: number } => entry.due !== null)
    .sort((a, b) => a.due - b.due);
  for (const { item, due } of dated) {
    if (due >= afterTomorrow) continue;
    const overdue = due < now.getTime();
    const row: DueRow = {
      id: item.work_object_id,
      when: due < todayStart ? monthDay.format(due) : formatClock(due),
      overdue,
      title: workItemTitle(item),
      note: `${sourceLabel(item)} · ${HANDLING_ACTION_LABELS[item.handling_action]}`,
    };
    if (overdue) groups.overdue.push(row);
    else if (due < tomorrowStart) groups.today.push(row);
    else groups.tomorrow.push(row);
  }
  return groups;
}

export interface CountBar {
  label: string;
  value: number;
}

/** 手上的事按「要我做什么」来分，多的排前面。 */
export function countByHandlingAction(items: readonly WorkItem[]): CountBar[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    const label = HANDLING_ACTION_LABELS[item.handling_action];
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
}

export interface DayColumn {
  /** 柱子下面的字：今天、明天，之后是周几。 */
  label: string;
  value: number;
  today: boolean;
}

const WEEKDAY_SHORT = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'] as const;

/** 从今天起 7 天，每天到期几件。 */
export function countNextSevenDays(items: readonly WorkItem[], now: Date): DayColumn[] {
  const columns: DayColumn[] = [];
  for (let offset = 0; offset < 7; offset += 1) {
    const from = startOfDay(now, offset);
    const to = startOfDay(now, offset + 1);
    const value = items.filter((item) => {
      const due = dueTime(item);
      return due !== null && due >= from && due < to;
    }).length;
    columns.push({
      label: offset === 0 ? '今天' : offset === 1 ? '明天' : WEEKDAY_SHORT[new Date(from).getDay()] ?? '',
      value,
      today: offset === 0,
    });
  }
  return columns;
}

export interface SentTask {
  id: string;
  title: string;
  done: number;
  total: number;
  /** 右上角那句状态：超期几天、今天到期，或截止日期。 */
  state: string;
  tone: 'hot' | 'warn' | 'plain';
  /** 截止时间与交办时间（毫秒）；没有或读不出来时是 null。 */
  due: number | null;
  created: number | null;
}

export interface SentSummary {
  active: number;
  dueToday: number;
  overdue: number;
  tasks: SentTask[];
}

function relationDone(relation: HandlingRelationView): boolean {
  return relation.status === 'submitted';
}

/** 我交办的事办得怎么样：超期的排最前，其次今天到期，再按截止时间。 */
export function summarizeSent(items: readonly WorkItem[], now: Date): SentSummary {
  const todayStart = startOfDay(now, 0);
  const tomorrowStart = startOfDay(now, 1);
  const entries = items
    .filter((item) => item.state_authority === 'internal')
    .map((item) => {
      const due = dueTime(item);
      const relations = (item.relations ?? []) as HandlingRelationView[];
      let state = '没有截止时间';
      let tone: SentTask['tone'] = 'plain';
      if (due !== null) {
        if (due < now.getTime()) {
          const days = Math.max(1, Math.ceil((todayStart - startOfDay(new Date(due), 0)) / 86_400_000));
          state = due >= todayStart ? '今天已过截止时间' : `超期 ${days} 天`;
          tone = 'hot';
        } else if (due < tomorrowStart) {
          state = `今天 ${formatClock(due)} 到期`;
          tone = 'warn';
        } else {
          state = `${monthDay.format(due)} 到期`;
        }
      }
      const task: SentTask = {
        id: item.work_object_id,
        title: workItemTitle(item),
        done: relations.filter(relationDone).length,
        total: relations.length,
        state,
        tone,
        due,
        created: item.created_at == null || Number.isNaN(Date.parse(item.created_at)) ? null : Date.parse(item.created_at),
      };
      return { task, due: due ?? Number.POSITIVE_INFINITY };
    })
    .sort((a, b) => a.due - b.due);
  return {
    active: entries.length,
    dueToday: entries.filter((entry) => entry.task.tone === 'warn').length,
    overdue: entries.filter((entry) => entry.task.tone === 'hot').length,
    tasks: entries.map((entry) => entry.task),
  };
}

export interface CompletedSummary {
  total: number;
  /** 有截止时间的办结事项里，按期办结的件数与总件数。 */
  onTime: number;
  withDue: number;
}

export function summarizeCompleted(items: readonly WorkItem[]): CompletedSummary {
  let onTime = 0;
  let withDue = 0;
  for (const item of items) {
    if (item.state_authority !== 'internal') continue;
    const due = dueTime(item);
    const completed = item.completed_at == null ? Number.NaN : Date.parse(item.completed_at);
    if (due === null || Number.isNaN(completed)) continue;
    withDue += 1;
    if (completed <= due) onTime += 1;
  }
  return { total: items.length, onTime, withDue };
}

export interface RecentRow {
  id: string;
  title: string;
  when: string;
}

/** OA 里最近收到的几件。 */
export function recentOaItems(items: readonly WorkItem[], now: Date, limit: number): RecentRow[] {
  const todayStart = startOfDay(now, 0);
  const yesterdayStart = startOfDay(now, -1);
  return items
    .filter((item): item is OAWorkObjectView => item.state_authority === 'external_snapshot')
    .map((item) => ({ item, received: Date.parse(item.source_received_at) }))
    .filter((entry) => !Number.isNaN(entry.received))
    .sort((a, b) => b.received - a.received)
    .slice(0, limit)
    .map(({ item, received }) => ({
      id: item.work_object_id,
      title: item.source_title,
      when: received >= todayStart ? '今天' : received >= yesterdayStart ? '昨天' : monthDay.format(received),
    }));
}

export function countOaItems(items: readonly WorkItem[]): number {
  return items.filter((item) => item.state_authority === 'external_snapshot').length;
}
