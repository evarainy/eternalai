import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Icon } from '../../shared/ui/Icon';
import { useAuthStore } from '../../stores/authStore';
import { formatClock, summarizeSent, useMinuteNow } from '../../shared/work/workSummaries';
import type { SentTask } from '../../shared/work/workSummaries';
import { readWorkQueue } from './workObjectLifecycleApi';
import styles from './DispatchStatus.module.css';

/**
 * 交办情况：我交办出去、还在办的事办得怎么样。
 *
 * 数据只有一处来源：工作事项接口里「我发出的、进行中」这一队列。办结统计、各承办单位的名称和
 * 一键督办都要新的后端能力，这里不摆占位，也不编数字。
 */
type Filter = 'active' | 'overdue' | 'today';

const FILTER_LABELS: Record<Filter, string> = {
  active: '进行中',
  overdue: '已超期',
  today: '今天到期',
};

const monthDay = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' });
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'] as const;

/** 进度格最多画这么多个，再多就改画一条进度条。 */
const MAX_PROGRESS_CELLS = 14;

function dueParts(task: SentTask, now: Date): { day: string; note: string } {
  if (task.due === null) return { day: '未设截止', note: '' };
  const due = new Date(task.due);
  const sameDay = due.toDateString() === now.toDateString();
  if (task.tone === 'hot') return { day: sameDay ? '今天' : monthDay.format(due), note: task.state };
  if (sameDay) return { day: '今天', note: formatClock(task.due) };
  return { day: monthDay.format(due), note: `${WEEKDAYS[due.getDay()] ?? ''} ${formatClock(task.due)}` };
}

function Progress({ done, total }: { done: number; total: number }) {
  if (total === 0) return <span className={styles.progressText}>还没有承办对象</span>;
  return (
    <div className={styles.progress}>
      <span className={`${styles.progressText} wb-num`}>{`${done} / ${total} 已提交`}</span>
      {total <= MAX_PROGRESS_CELLS ? (
        <span aria-hidden="true" className={styles.cells}>
          {Array.from({ length: total }, (_, index) => (
            <i data-on={index < done ? 'true' : 'false'} key={index} />
          ))}
        </span>
      ) : (
        <span aria-hidden="true" className={styles.bar}>
          <i style={{ width: `${Math.round((done / total) * 100)}%` }} />
        </span>
      )}
    </div>
  );
}

export function DispatchStatus({ newHref }: { newHref: string }) {
  const generation = useAuthStore((state) => state.generation);
  const [filter, setFilter] = useState<Filter>('active');
  const now = useMinuteNow();
  const query = useQuery({
    queryKey: ['work-dispatch', generation, 'sent-active'] as const,
    queryFn: () => readWorkQueue({ queue: 'mine_sent', completion: 'active' }),
  });
  const summary = useMemo(
    () => (query.data === undefined ? null : summarizeSent(query.data.items, now)),
    [query.data, now],
  );

  if (summary === null) {
    return query.isError ? (
      <div className={styles.state} role="alert">
        <Icon name="alert" size={24} />
        <b>暂时取不到交办情况</b>
        <span>其他页面不受影响。</span>
        <button className="wb-btn" onClick={() => void query.refetch()} type="button">
          <Icon name="retry" size={16} />
          重试
        </button>
      </div>
    ) : (
      <div aria-label="正在读取交办情况" className={styles.skeleton} role="status">
        <i /><i /><i /><i />
      </div>
    );
  }

  const truncated = query.data !== undefined
    && (query.data.limit_exceeded || query.data.next_cursor != null);
  const counts: Record<Filter, number> = {
    active: summary.active,
    overdue: summary.overdue,
    today: summary.dueToday,
  };
  const tasks = summary.tasks.filter((task) => (
    filter === 'active' ? true : filter === 'overdue' ? task.tone === 'hot' : task.tone === 'warn'
  ));
  const submitted = summary.tasks.reduce((sum, task) => sum + task.done, 0);
  const targets = summary.tasks.reduce((sum, task) => sum + task.total, 0);

  // 有上一次的数据时刷新失败：照常显示上一次的情况，但要说明这不是刚读到的，并给重试。
  const stale = query.isError ? (
    <p className={styles.stale} role="alert">
      <Icon name="alert" size={16} />
      刚才刷新没有成功，下面是上一次读到的情况。
      <button className="wb-btn wb-btn-sm" onClick={() => void query.refetch()} type="button">重试</button>
    </p>
  ) : null;

  if (summary.active === 0) {
    return (
      <div className={styles.status}>
        {stale}
        <div className={styles.state}>
          <Icon name="check" size={24} />
          <b>现在没有交办出去、还在办的事</b>
          <span>交办一件事后，这里能看到谁办完了、谁还没办。</span>
          <Link className="wb-btn wb-btn-primary" to={newHref}>
            <Icon name="plus" size={16} />
            新建交办
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.status}>
      {stale}
      <div className={styles.overview}>
        <section className={styles.ov}>
          <header><h2>我交办的</h2><small>进行中</small></header>
          <div className={styles.kpis}>
            <div><b className="wb-num">{summary.active}</b><span>{truncated ? '进行中（至少）' : '进行中'}</span></div>
            <div><b className="wb-num">{summary.dueToday}</b><span>今天到期</span></div>
            <div data-hot={summary.overdue > 0 ? 'true' : 'false'}>
              <b className="wb-num">{summary.overdue}</b><span>已超期</span>
            </div>
          </div>
        </section>
        <section className={styles.ov}>
          <header><h2>承办进度</h2><small>{`进行中的 ${summary.active} 项`}</small></header>
          {targets === 0 ? (
            <p className={styles.ovNote}>这些事还没有承办对象。</p>
          ) : (
            <div className={styles.meter}>
              <p>承办对象已提交<span className="wb-num">{`${submitted} / ${targets}`}</span></p>
              <div><i style={{ width: `${Math.round((submitted / targets) * 100)}%` }} /></div>
            </div>
          )}
          {truncated ? <p className={styles.ovNote}>事项较多，这里只统计已读取到的部分。</p> : null}
        </section>
      </div>

      <div aria-label="交办筛选" className="wb-seg" role="group">
        {(Object.keys(FILTER_LABELS) as Filter[]).map((key) => (
          <button
            aria-pressed={filter === key}
            key={key}
            onClick={() => setFilter(key)}
            type="button"
          >
            {FILTER_LABELS[key]}
            <i className="wb-num">{counts[key]}</i>
          </button>
        ))}
      </div>

      {tasks.length === 0 ? (
        <p className={styles.none}>
          {truncated ? `已读取的部分里没有${FILTER_LABELS[filter]}的交办。` : `没有${FILTER_LABELS[filter]}的交办。`}
        </p>
      ) : (
        <ul className={styles.rows}>
          {tasks.map((task) => {
            const due = dueParts(task, now);
            return (
              <li key={task.id}>
                <Link className={styles.row} to={`/work-objects?work_object_id=${encodeURIComponent(task.id)}`}>
                  <span className={`${styles.due} wb-num`} data-hot={task.tone === 'hot' ? 'true' : 'false'}>
                    {due.day}
                    <small>{due.note}</small>
                  </span>
                  <span className={styles.text}>
                    <b>{task.title}</b>
                    <small>
                      {task.total === 0 ? '还没有承办对象' : `交办给 ${task.total} 个对象`}
                      {task.created === null ? '' : ` · ${monthDay.format(task.created)}交办`}
                    </small>
                  </span>
                  <Progress done={task.done} total={task.total} />
                  <Icon name="right" size={17} />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
