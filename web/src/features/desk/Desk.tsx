import { Fragment, memo, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, DragEvent, ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { oaWorkbenchNavigation } from '../../contracts/runtimeProjection';
import { getBindingApiV1CredentialBindingsTargetSystemGet as getBinding } from '../../generated/credential-bindings/credential-bindings';
import { Icon } from '../../shared/ui/Icon';
import type { IconName } from '../../shared/ui/Icon';
import { useAuthStore } from '../../stores/authStore';
import {
  countByHandlingAction,
  countNextSevenDays,
  countOaItems,
  formatClock,
  groupByDue,
  recentOaItems,
  summarizeCompleted,
  summarizeSent,
  useCompletedWork,
  useMinuteNow,
  useOpenWork,
  useSentWork,
} from './deskData';
import type { DueRow } from './deskData';
import { CARD_IDS, CARD_SIZES, CARD_SIZE_LABELS, DEFAULT_COLUMNS, useHomeLayoutStore } from './homeLayout';
import type { CardId, CardSize } from './homeLayout';
import styles from './Desk.module.css';

/**
 * 首页：助手页上的一张张卡片，像桌上摆着的纸。
 *
 * - 卡片三档固定高度（小 / 中 / 大，占 2 / 3 / 4 个高度单位），一列凑满 6 个单位正好一屏；
 *   内容多了在卡片里滚。滚动条平时不出现，鼠标移上去才出现。
 * - 每张卡片都能直接拿去问 AI：点标题行右边的记号，把一句现成的话填进输入框。
 * - 只放有真实数据来源的卡片。摆哪些、怎么排、多大，每个人可以自己定（见 `homeLayout.ts`）。
 */
const CARD_TITLES: Record<CardId, string> = {
  todo: '今日要办', types: '待办按类型', tasks: '交办情况', week: '未来 7 天到期', oa: 'OA 办公', done: '近 30 天办结',
};

/** 卡片库里每张卡片下面的一句话。 */
const CARD_NOTES: Record<CardId, string> = {
  todo: '已超期、今天、明天到期的事',
  types: '待办按办理动作分类的数量',
  tasks: '我交办出去、还在办的事',
  week: '未来一周每天到期几件',
  oa: 'OA 里等我办的流程',
  done: '最近一个月办结的事',
};

type Ask = (question: string) => void;

/** 服务端一次最多给这么多条；超过时数字是「至少」，不能说成总数。 */
function countText(count: number, truncated: boolean, unit: string): string {
  return truncated ? `至少 ${count} ${unit}` : `共 ${count} ${unit}`;
}

/** 取数的三种非正常状态都画在卡片里，卡片位置和大小不变。 */
function CardState({
  action,
  icon,
  note,
  text,
}: {
  action?: ReactNode;
  icon: IconName;
  note?: string;
  text: string;
}) {
  return (
    <div className={styles.state}>
      <Icon name={icon} size={22} />
      <span>{text}</span>
      {note === undefined ? null : <small>{note}</small>}
      {action}
    </div>
  );
}

function CardSkeleton() {
  return (
    <div aria-label="正在读取" className={styles.skeleton} role="status">
      <i /><i /><i /><i />
    </div>
  );
}

function queryBody<T>(query: UseQueryResult<T>, render: (data: T) => ReactNode): ReactNode {
  if (query.data !== undefined) return render(query.data);
  if (query.isError) {
    return (
      <CardState
        action={(
          <button className="wb-btn wb-btn-sm" onClick={() => { void query.refetch(); }} type="button">
            重试
          </button>
        )}
        icon="alert"
        text="暂时取不到数据"
      />
    );
  }
  return <CardSkeleton />;
}

function DeskCard({
  ask,
  children,
  foot,
  onAsk,
  size,
  sub,
  title,
}: {
  ask: string;
  children: ReactNode;
  foot?: ReactNode;
  onAsk: Ask;
  size: CardSize | 'auto';
  sub?: string;
  title: string;
}) {
  return (
    <article aria-label={title} className={styles.card} data-size={size}>
      <header className={styles.cardHead}>
        <h2>{title}</h2>
        {sub === undefined ? null : <small>{sub}</small>}
        <button
          aria-label={`就「${title}」问 AI`}
          className={styles.ask}
          onClick={() => onAsk(ask)}
          title="就这张卡片问 AI"
          type="button"
        >
          <Icon name="sparkle" size={17} />
        </button>
      </header>
      <div className={styles.cardBody}>{children}</div>
      {foot === undefined ? null : <footer className={styles.cardFoot}>{foot}</footer>}
    </article>
  );
}

function workObjectHref(id: string): string {
  return `/work-objects?work_object_id=${encodeURIComponent(id)}`;
}

function DueRows({ label, rows }: { label: string; rows: readonly DueRow[] }) {
  if (rows.length === 0) return null;
  return (
    <>
      <div className={styles.rowsGroup}>{label}</div>
      {rows.map((row) => (
        <Link className={styles.row} key={row.id} to={workObjectHref(row.id)}>
          <span className={`${styles.due} wb-num`} data-hot={row.overdue ? 'true' : 'false'}>{row.when}</span>
          <span className={styles.rowText}>
            <b>{row.title}</b>
            <small>{row.note}</small>
          </span>
          <Icon className={styles.chevron} name="right" size={17} />
        </Link>
      ))}
    </>
  );
}

type CardProps = { onAsk: Ask; size: CardSize | 'auto' };

function TodoCard({ onAsk, size }: CardProps) {
  const query = useOpenWork();
  const now = useMinuteNow();
  const groups = useMemo(
    () => (query.data === undefined ? null : groupByDue(query.data.items, now)),
    [query.data, now],
  );
  const sub = groups === null
    ? undefined
    : [
      groups.overdue.length > 0 ? `已超期 ${groups.overdue.length} 件` : null,
      `今天 ${groups.today.length} 件`,
      `明天 ${groups.tomorrow.length} 件`,
    ].filter(Boolean).join(' · ');
  return (
    <DeskCard
      ask="今天要办的事，先做哪件？"
      foot={<Link className={styles.more} to="/work-objects">全部事项<Icon name="right" size={15} /></Link>}
      onAsk={onAsk}
      size={size}
      sub={sub}
      title="今日要办"
    >
      {queryBody(query, () => (
        groups === null || groups.overdue.length + groups.today.length + groups.tomorrow.length === 0
          ? <CardState icon="check" text="今天和明天没有到期的事" />
          : (
            <>
              <DueRows label="已超期" rows={groups.overdue} />
              <DueRows label="今天" rows={groups.today} />
              <DueRows label="明天" rows={groups.tomorrow} />
            </>
          )
      ))}
    </DeskCard>
  );
}

function TypesCard({ onAsk, size }: CardProps) {
  const query = useOpenWork();
  const bars = useMemo(
    () => (query.data === undefined ? [] : countByHandlingAction(query.data.items)),
    [query.data],
  );
  const max = Math.max(1, ...bars.map((bar) => bar.value));
  return (
    <DeskCard
      ask="我的待办里哪类事情最多？"
      onAsk={onAsk}
      size={size}
      sub={query.data === undefined ? undefined : countText(query.data.items.length, query.data.limit_exceeded, '件')}
      title="待办按类型"
    >
      {queryBody(query, () => (
        bars.length === 0
          ? <CardState icon="check" text="现在没有待办" />
          : (
            <div className={styles.bars}>
              {bars.map((bar) => (
                <div className={styles.bar} key={bar.label}>
                  <span>{bar.label}</span>
                  <span className={styles.barTrack}><i style={{ width: `${(bar.value / max) * 100}%` }} /></span>
                  <b className="wb-num">{bar.value}</b>
                </div>
              ))}
            </div>
          )
      ))}
    </DeskCard>
  );
}

function TasksCard({ onAsk, size }: CardProps) {
  const query = useSentWork();
  const now = useMinuteNow();
  const summary = useMemo(
    () => (query.data === undefined ? null : summarizeSent(query.data.items, now)),
    [query.data, now],
  );
  const more = query.data !== undefined && (query.data.limit_exceeded || query.data.next_cursor != null);
  return (
    <DeskCard
      ask="我交办的事里哪些还没办完？"
      foot={<Link className={styles.more} to="/work-dispatch">全部交办<Icon name="right" size={15} /></Link>}
      onAsk={onAsk}
      size={size}
      sub={summary === null ? undefined : `进行中${more ? '至少' : ''} ${summary.active} 项`}
      title="交办情况"
    >
      {queryBody(query, () => (
        summary === null || summary.tasks.length === 0
          ? <CardState icon="check" text="现在没有交办出去、还在办的事" />
          : (
            <>
              <div className={styles.counts}>
                <span><b className="wb-num">{summary.active}</b>进行中</span>
                <span><b className="wb-num">{summary.dueToday}</b>今天到期</span>
                <span data-hot={summary.overdue > 0 ? 'true' : 'false'}>
                  <b className="wb-num">{summary.overdue}</b>已超期
                </span>
              </div>
              {summary.tasks.map((task) => (
                <Link className={styles.task} key={task.id} to={workObjectHref(task.id)}>
                  <b>{task.title}</b>
                  <span className={styles.taskState} data-tone={task.tone}>{task.state}</span>
                  {task.total === 0 ? null : (
                    <>
                      <span aria-hidden="true" className={styles.progress}>
                        {Array.from({ length: task.total }, (_, index) => (
                          <i data-on={index < task.done ? 'true' : 'false'} key={index} />
                        ))}
                      </span>
                      <span className={`${styles.taskCount} wb-num`}>{`${task.done} / ${task.total}`}</span>
                    </>
                  )}
                </Link>
              ))}
            </>
          )
      ))}
    </DeskCard>
  );
}

function WeekCard({ onAsk, size }: CardProps) {
  const query = useOpenWork();
  const now = useMinuteNow();
  const columns = useMemo(
    () => (query.data === undefined ? [] : countNextSevenDays(query.data.items, now)),
    [query.data, now],
  );
  const total = columns.reduce((sum, column) => sum + column.value, 0);
  const max = Math.max(1, ...columns.map((column) => column.value));
  return (
    <DeskCard
      ask="未来一周哪天到期的事最多？"
      onAsk={onAsk}
      size={size}
      sub={query.data === undefined ? undefined : countText(total, query.data.limit_exceeded, '件')}
      title="未来 7 天到期"
    >
      {queryBody(query, () => (
        total === 0
          ? <CardState icon="check" text="未来 7 天没有到期的事" />
          : (
            <div className={styles.columns}>
              {columns.map((column) => (
                <div data-today={column.today ? 'true' : 'false'} key={column.label} title={`${column.label} ${column.value} 件`}>
                  <span className={styles.columnBar}>
                    <em className="wb-num">{column.value === 0 ? '' : column.value}</em>
                    <i style={{ height: `${(column.value / max) * 100}%` }} />
                  </span>
                  <span className={styles.columnLabel}>{column.label}</span>
                </div>
              ))}
            </div>
          )
      ))}
    </DeskCard>
  );
}

function OaCard({ onAsk, size }: CardProps) {
  const generation = useAuthStore((state) => state.generation);
  const work = useOpenWork();
  // 与外壳的系统状态共用同一份缓存。
  const binding = useQuery({
    queryKey: ['credential-binding', generation, 'oa'] as const,
    queryFn: () => getBinding('oa'),
  });
  const navigation = oaWorkbenchNavigation();
  const now = useMinuteNow();
  const sync = work.data?.oa_sync;
  const synced = sync?.last_success_at ?? null;
  const needsBinding = binding.data !== undefined
    && (!binding.data.bound || binding.data.poll_status === 'invalid' || binding.data.poll_status === 'captcha_required');
  // 列表接口成功不等于 OA 同步成功：从没同步成功过，就不能说「OA 里没有待办」。
  const neverSynced = sync !== undefined && synced === null;
  const syncFailed = sync !== undefined && (sync.status === 'failed' || sync.status === 'unsupported_scope');
  const sub = synced === null
    ? undefined
    : `${formatClock(Date.parse(synced))} 同步${syncFailed ? ' · 最近一次没同步成功' : ''}`;
  return (
    <DeskCard
      ask="OA 里有什么要我处理的？"
      foot={navigation.kind === 'allowed' ? (
        <a className={styles.more} href={navigation.href} rel="noopener noreferrer" target="_blank">
          打开 OA 办公<Icon name="open" size={15} />
        </a>
      ) : undefined}
      onAsk={onAsk}
      size={size}
      sub={sub}
      title="OA 办公"
    >
      {needsBinding ? (
        <CardState
          action={<Link className="wb-btn wb-btn-sm" to="/settings/bindings">绑定账号</Link>}
          icon="lock"
          note="绑定后这里显示你的 OA 待办"
          text={binding.data?.bound === true ? 'OA 登录已失效' : '还没有绑定 OA 账号'}
        />
      ) : queryBody(work, (data) => {
        if (neverSynced) {
          return (
            <CardState
              action={<Link className="wb-btn wb-btn-sm" to="/work-objects">去工作事项刷新</Link>}
              icon={syncFailed ? 'alert' : 'clock'}
              note={binding.isError ? '也读不到 OA 账号的绑定状态' : undefined}
              text={syncFailed ? 'OA 数据没有同步成功' : '还没有取到 OA 数据'}
            />
          );
        }
        const groups = groupByDue(data.items.filter((item) => item.state_authority === 'external_snapshot'), now);
        const recent = recentOaItems(data.items, now, 5);
        return (
          <>
            <div className={styles.kpis}>
              <span><b className="wb-num">{countOaItems(data.items)}{data.limit_exceeded ? '+' : ''}</b>待办</span>
              <span><b className="wb-num">{groups.today.length + groups.tomorrow.length}</b>今明到期</span>
              <span data-hot={groups.overdue.length > 0 ? 'true' : 'false'}>
                <b className="wb-num">{groups.overdue.length}</b>已超期
              </span>
            </div>
            {recent.length === 0 ? <p className={styles.note}>OA 里现在没有待办。</p> : recent.map((row) => (
              <Link className={styles.listRow} key={row.id} to={workObjectHref(row.id)}>
                <span>{row.title}</span>
                <small className="wb-num">{row.when}</small>
              </Link>
            ))}
          </>
        );
      })}
    </DeskCard>
  );
}

function DoneCard({ onAsk, size }: CardProps) {
  const query = useCompletedWork();
  const summary = useMemo(
    () => (query.data === undefined ? null : summarizeCompleted(query.data.items)),
    [query.data],
  );
  const doneTruncated = query.data !== undefined && (query.data.limit_exceeded || query.data.next_cursor != null);
  return (
    <DeskCard
      ask="最近 30 天我办结了哪些事？"
      onAsk={onAsk}
      size={size}
      sub="工作台内的事项"
      title="近 30 天办结"
    >
      {queryBody(query, () => (
        summary === null ? null : (
          <>
            <div className={styles.figure}>
              <b className="wb-num">{summary.total}</b>
              <span>{doneTruncated ? '件以上' : '件'}</span>
            </div>
            {summary.withDue === 0 ? null : (
              <div className={styles.meter}>
                <p>按期办结<span className="wb-num">{`${summary.onTime} / ${summary.withDue}`}</span></p>
                <div><i style={{ width: `${(summary.onTime / summary.withDue) * 100}%` }} /></div>
              </div>
            )}
            <p className={styles.note}>OA 里已办结的事还没有接进来，这里只算工作台内交办的事项。</p>
          </>
        )
      ))}
    </DeskCard>
  );
}

const CARD_COMPONENTS: Record<CardId, (props: CardProps) => ReactNode> = {
  todo: TodoCard,
  types: TypesCard,
  tasks: TasksCard,
  week: WeekCard,
  oa: OaCard,
  done: DoneCard,
};

interface Stat {
  label: string;
  value: number | null;
  hot: boolean;
  ask: string;
}

function useStats(): Stat[] {
  const open = useOpenWork();
  const completed = useCompletedWork();
  const now = useMinuteNow();
  return useMemo(() => {
    const groups = open.data === undefined ? null : groupByDue(open.data.items, now);
    const stats: Stat[] = [
      { label: '今日到期', value: groups?.today.length ?? null, hot: false, ask: '今天到期的事分别是什么？' },
      { label: '明天到期', value: groups?.tomorrow.length ?? null, hot: false, ask: '明天到期的有哪些？' },
      { label: '近 30 天办结', value: completed.data?.items.length ?? null, hot: false, ask: '最近 30 天我办结了哪些事？' },
    ];
    if (groups !== null && groups.overdue.length > 0) {
      stats.unshift({ label: '已超期', value: groups.overdue.length, hot: true, ask: '已经超期的事有哪些？先处理哪件？' });
    }
    return stats;
  }, [open.data, completed.data, now]);
}

/** 数字从旧值滚到新值。精简动效或系统要求减少动效时直接显示。 */
function AnimatedNumber({ value }: { value: number }) {
  const [shown, setShown] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    const still = document.documentElement.dataset.fx === 'lite'
      || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
      || typeof requestAnimationFrame !== 'function';
    if (still || from.current === value) {
      from.current = value;
      setShown(value);
      return undefined;
    }
    const start = from.current;
    const startedAt = performance.now();
    let frame = requestAnimationFrame(function tick(now: number) {
      const progress = Math.min(1, (now - startedAt) / 700);
      const eased = 1 - (1 - progress) ** 3;
      setShown(Math.round(start + (value - start) * eased));
      if (progress < 1) frame = requestAnimationFrame(tick);
      else from.current = value;
    });
    return () => { cancelAnimationFrame(frame); from.current = value; };
  }, [value]);
  return <>{shown}</>;
}

/** 重点数字：点任何一个数字，就把对应的问题填进输入框。 */
function Stats({ onAsk }: { onAsk: Ask }) {
  const stats = useStats();
  return (
    <div className={styles.stats}>
      {stats.map((stat) => (
        <button
          className={styles.stat}
          data-hot={stat.hot ? 'true' : 'false'}
          key={stat.label}
          onClick={() => onAsk(stat.ask)}
          type="button"
        >
          <b className="wb-num">{stat.value === null ? '–' : <AnimatedNumber value={stat.value} />}</b>
          <span>{stat.label}</span>
        </button>
      ))}
    </div>
  );
}

const DEFAULT_SIZES = Object.fromEntries(
  DEFAULT_COLUMNS.flat().map((slot) => [slot.id, slot.size]),
) as Record<CardId, CardSize>;

/**
 * 首页。平时是一张张摆好的卡片；自定义时卡片可以拖到别的位置（包括别的列）、改大小、移出，
 * 每列末尾可以把没摆出来的卡片加回来。
 */
export const Desk = memo(function Desk({ onAsk }: { onAsk: Ask }) {
  const columns = useHomeLayoutStore((state) => state.columns);
  const customizing = useHomeLayoutStore((state) => state.customizing);
  const place = useHomeLayoutStore((state) => state.place);
  const resize = useHomeLayoutStore((state) => state.resize);
  const remove = useHomeLayoutStore((state) => state.remove);
  const [dragging, setDragging] = useState<CardId | null>(null);
  const [drop, setDrop] = useState<{ column: number; index: number } | null>(null);
  const [libraryColumn, setLibraryColumn] = useState<number | null>(null);
  const placed = new Set(columns.flat().map((slot) => slot.id));
  const spare = CARD_IDS.filter((id) => !placed.has(id));

  // 退出自定义时把拖放和卡片库的临时状态清掉。
  useEffect(() => {
    if (!customizing) { setDragging(null); setDrop(null); setLibraryColumn(null); }
  }, [customizing]);

  /** 鼠标在这一列的哪两张卡片之间：按各卡片的中线判断。 */
  const dropIndex = (event: DragEvent<HTMLDivElement>): number => {
    const slots = [...event.currentTarget.querySelectorAll<HTMLElement>('[data-slot]')];
    const index = slots.findIndex((slot) => {
      const box = slot.getBoundingClientRect();
      return event.clientY < box.top + box.height / 2;
    });
    return index < 0 ? slots.length : index;
  };

  let order = 0;
  return (
    <div className={styles.desk} data-customizing={customizing ? 'true' : 'false'}>
      <header className={styles.deskHead}>
        <Stats onAsk={onAsk} />
      </header>
      <div className={styles.cards} style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` }}>
        {columns.map((column, columnIndex) => (
          <div
            className={styles.column}
            key={columnIndex}
            onDragOver={customizing && dragging !== null ? (event) => {
              event.preventDefault();
              const index = dropIndex(event);
              setDrop((current) => (current?.column === columnIndex && current.index === index
                ? current : { column: columnIndex, index }));
            } : undefined}
            onDrop={customizing && dragging !== null ? (event) => {
              event.preventDefault();
              place(dragging, columnIndex, dropIndex(event));
              setDragging(null);
              setDrop(null);
            } : undefined}
          >
            {column.map((slot, index) => {
              const Card = CARD_COMPONENTS[slot.id];
              const style = { '--i': order } as CSSProperties;
              order += 1;
              return (
                <Fragment key={slot.id}>
                  {drop?.column === columnIndex && drop.index === index ? <i aria-hidden="true" className={styles.dropLine} /> : null}
                  <div
                    className={styles.slot}
                    data-dragging={dragging === slot.id ? 'true' : 'false'}
                    data-slot=""
                    draggable={customizing}
                    onDragEnd={() => { setDragging(null); setDrop(null); }}
                    onDragStart={customizing ? (event) => {
                      event.dataTransfer.effectAllowed = 'move';
                      event.dataTransfer.setData('text/plain', slot.id);
                      setDragging(slot.id);
                      setLibraryColumn(null);
                    } : undefined}
                    style={style}
                  >
                    <Card onAsk={onAsk} size={slot.size} />
                    {customizing ? (
                      <div className={styles.slotTools}>
                        <div aria-label={`${CARD_TITLES[slot.id]}的大小`} className={styles.sizes} role="group">
                          {CARD_SIZES.map((size) => (
                            <button aria-pressed={slot.size === size} key={size} onClick={() => resize(slot.id, size)} type="button">
                              {CARD_SIZE_LABELS[size]}
                            </button>
                          ))}
                        </div>
                        <button
                          aria-label={`把「${CARD_TITLES[slot.id]}」移出首页`}
                          className={styles.slotRemove}
                          onClick={() => remove(slot.id)}
                          title="移出首页"
                          type="button"
                        >
                          <Icon name="close" size={15} strokeWidth={2.2} />
                        </button>
                      </div>
                    ) : null}
                  </div>
                </Fragment>
              );
            })}
            {drop?.column === columnIndex && drop.index === column.length ? <i aria-hidden="true" className={styles.dropLine} /> : null}
            {customizing ? (
              <div className={styles.addWrap}>
                <button
                  aria-expanded={libraryColumn === columnIndex}
                  className={styles.add}
                  onClick={() => setLibraryColumn((open) => (open === columnIndex ? null : columnIndex))}
                  type="button"
                >
                  <Icon name="plus" size={17} />
                  添加卡片
                </button>
                {libraryColumn === columnIndex ? (
                  <div aria-label="卡片库" className={styles.library} role="group">
                    {spare.length === 0 ? <p>卡片都已经在首页上了。</p> : spare.map((id) => (
                      <button
                        key={id}
                        onClick={() => {
                          place(id, columnIndex, column.length, DEFAULT_SIZES[id]);
                          setLibraryColumn(null);
                        }}
                        type="button"
                      >
                        <b>{CARD_TITLES[id]}</b>
                        <small>{CARD_NOTES[id]}</small>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
});

/** 自定义首页时放在输入框位置上的操作条。 */
export function HomeCustomizeBar() {
  const reset = useHomeLayoutStore((state) => state.reset);
  const finish = useHomeLayoutStore((state) => state.finishCustomizing);
  return (
    <div aria-label="自定义首页" className={styles.customizeBar} role="toolbar">
      <Icon name="grid" size={18} />
      <span>拖动卡片换位置，卡片右上角改大小或移出</span>
      <button className="wb-btn wb-btn-sm" onClick={reset} type="button">恢复默认</button>
      <button className="wb-btn wb-btn-primary wb-btn-sm" onClick={finish} type="button">
        <Icon name="check" size={16} strokeWidth={2.2} />
        完成
      </button>
    </div>
  );
}
