import { useMemo, useState } from 'react';
import {
  countNextSevenDays,
  formatClock,
  summarizeCompleted,
  useMinuteNow,
  workItemTitle,
} from '../../shared/work/workSummaries';
import type { WorkItem } from '../../shared/work/workSummaries';
import styles from './WorkOverview.module.css';

/**
 * 工作事项页顶部的概览带：今天的时间线、未来 7 天到期、近 30 天办结。
 * 只用这一页本来就读到的两份列表来算，不另外取数；列表还没读到时显示灰条，位置不变。
 */
interface ListSlice {
  items: readonly WorkItem[];
  /** 服务端只给了前一部分：数字要写成「至少」。 */
  truncated: boolean;
}

/** 时间线画 8 点到 20 点；更早或更晚到期的事贴在两端。 */
const DAY_START_HOUR = 8;
const DAY_END_HOUR = 20;
/** 时间线上的点多于这个数，钟点上下错开也排不下，只画点，钟点留给悬停提示。 */
const MAX_LABELLED_EVENTS = 6;
/** 两件事在时间线上相距不到这么多（占整条线的百分比）就合成一个点，点开再选。 */
const CLUSTER_GAP = 3;

function dayPosition(timestamp: number, dayStart: number): number {
  const from = dayStart + DAY_START_HOUR * 3_600_000;
  const span = (DAY_END_HOUR - DAY_START_HOUR) * 3_600_000;
  return Math.min(100, Math.max(0, ((timestamp - from) / span) * 100));
}

interface TodayEvent {
  id: string;
  due: number;
  title: string;
  overdue: boolean;
  left: number;
}

function todayEvents(items: readonly WorkItem[], now: Date): TodayEvent[] {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const dayStart = start.getTime();
  const dayEnd = dayStart + 86_400_000;
  return items
    .flatMap((item) => {
      const due = item.due_at === null ? Number.NaN : Date.parse(item.due_at);
      if (Number.isNaN(due) || due < dayStart || due >= dayEnd) return [];
      return [{
        id: item.work_object_id,
        due,
        title: workItemTitle(item),
        overdue: due < now.getTime(),
        left: dayPosition(due, dayStart),
      }];
    })
    .sort((a, b) => a.due - b.due);
}

interface EventCluster {
  left: number;
  overdue: boolean;
  /** 至少一件。 */
  events: [TodayEvent, ...TodayEvent[]];
}

/** 挨在一起的事合成一个点：同一钟点的几件事、晚上 8 点以后贴在右端的事，都点得到。 */
function clusterEvents(events: readonly TodayEvent[]): EventCluster[] {
  const clusters: EventCluster[] = [];
  for (const event of events) {
    const last = clusters[clusters.length - 1];
    if (last !== undefined && event.left - last.left < CLUSTER_GAP) {
      last.events.push(event);
      last.overdue = last.overdue || event.overdue;
    } else {
      clusters.push({ left: event.left, overdue: event.overdue, events: [event] });
    }
  }
  return clusters;
}

function Skeleton() {
  return (
    <div aria-label="正在读取" className={styles.skeleton} role="status">
      <i /><i />
    </div>
  );
}

export function WorkOverview({
  completed,
  onOpen,
  open,
}: {
  completed: ListSlice | undefined;
  onOpen: (workObjectId: string) => void;
  open: ListSlice | undefined;
}) {
  const now = useMinuteNow();
  const events = useMemo(() => (open === undefined ? [] : todayEvents(open.items, now)), [open, now]);
  const clusters = useMemo(() => clusterEvents(events), [events]);
  const [pickedLeft, setPickedLeft] = useState<number | null>(null);
  const picked = clusters.find((cluster) => cluster.left === pickedLeft && cluster.events.length > 1);
  const columns = useMemo(() => (open === undefined ? [] : countNextSevenDays(open.items, now)), [open, now]);
  const done = useMemo(() => (completed === undefined ? null : summarizeCompleted(completed.items)), [completed]);
  const weekTotal = columns.reduce((sum, column) => sum + column.value, 0);
  const weekMax = Math.max(1, ...columns.map((column) => column.value));
  const dayStart = new Date(now);
  dayStart.setHours(0, 0, 0, 0);
  const nowLeft = dayPosition(now.getTime(), dayStart.getTime());
  const labelled = clusters.length <= MAX_LABELLED_EVENTS;

  return (
    <div className={styles.overview}>
      <section aria-label="今天到期" className={styles.ov}>
        <header>
          <h2>今天</h2>
          {open === undefined ? null : (
            <small>
              {events.length === 0
                ? open.truncated ? '已读取的部分里没有到期的事' : '没有到期的事'
                : `${open.truncated ? '至少 ' : ''}${events.length} 件到期`}
            </small>
          )}
        </header>
        {open === undefined ? <Skeleton /> : (
          <div className={styles.track}>
            <i className={styles.past} style={{ width: `${nowLeft}%` }} />
            <i className={styles.now} style={{ left: `${nowLeft}%` }} title={`现在 ${formatClock(now.getTime())}`} />
            {clusters.map((cluster, index) => {
              const first = cluster.events[0];
              const single = cluster.events.length === 1;
              const name = single
                ? `${formatClock(first.due)} ${first.title}`
                : `${formatClock(first.due)} 前后 ${cluster.events.length} 件事`;
              return (
                <button
                  aria-expanded={single ? undefined : picked === cluster}
                  aria-label={name}
                  className={styles.event}
                  data-hot={cluster.overdue ? 'true' : 'false'}
                  data-many={single ? 'false' : 'true'}
                  data-up={index % 2 === 1 ? 'true' : 'false'}
                  key={first.id}
                  onClick={() => {
                    if (single) onOpen(first.id);
                    else setPickedLeft(picked === cluster ? null : cluster.left);
                  }}
                  style={{ left: `${cluster.left}%` }}
                  title={name}
                  type="button"
                >
                  {single ? null : <b className="wb-num">{cluster.events.length}</b>}
                  {labelled ? <span className="wb-num">{formatClock(first.due)}</span> : null}
                </button>
              );
            })}
            {picked === undefined ? null : (
              <ul aria-label="这个时间前后到期的事" className={styles.pick}>
                {picked.events.map((event) => (
                  <li key={event.id}>
                    <button data-hot={event.overdue ? 'true' : 'false'} onClick={() => onOpen(event.id)} type="button">
                      <span className="wb-num">{formatClock(event.due)}</span>
                      {event.title}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <section aria-label="未来 7 天到期" className={styles.ov}>
        <header>
          <h2>未来 7 天到期</h2>
          {open === undefined ? null : <small>{`${open.truncated ? '至少' : '共'} ${weekTotal} 件`}</small>}
        </header>
        {open === undefined ? <Skeleton /> : (
          <div className={styles.columns}>
            {columns.map((column) => (
              <div data-today={column.today ? 'true' : 'false'} key={column.label} title={`${column.label} ${column.value} 件`}>
                <span className={styles.columnBar}>
                  <em className="wb-num">{column.value === 0 ? '' : column.value}</em>
                  <i style={{ height: `${(column.value / weekMax) * 100}%` }} />
                </span>
                <span className={styles.columnLabel}>{column.label}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section aria-label="近 30 天办结" className={styles.ov}>
        <header>
          <h2>近 30 天办结</h2>
          <small>工作台内的事项</small>
        </header>
        {done === null || completed === undefined ? <Skeleton /> : (
          <>
            <div className={styles.figure}>
              <b className="wb-num">{done.total}</b>
              <span>{completed.truncated ? '件以上' : '件'}</span>
            </div>
            {done.withDue === 0 ? null : (
              <div className={styles.meter}>
                <p>按期办结<span className="wb-num">{`${done.onTime} / ${done.withDue}`}</span></p>
                <div><i style={{ width: `${(done.onTime / done.withDue) * 100}%` }} /></div>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
