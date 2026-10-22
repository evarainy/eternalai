import { normalizeSearchQuery, normalizeSearchValue } from '../../shared/query/workObjectSearchNormalization';
import { useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Empty, Flex, Select, Space, Tag, Typography } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError } from '../../api/mutator';
import { usePageContextRegistration } from '../../app/usePageContextRegistration';
import type { PageContextDeclaration } from '../../contracts/pageContext';
import { accessLost, logDispatchFailure } from './dispatchErrors';
import { lifecycleErrorText, readWorkQueue } from './workObjectLifecycleApi';
import { useAIDockStore } from '../../stores/aiDockStore';
import type {
  WorkObjectListResponseItemsItem,
} from '../../generated/work-objects/work-objects.schemas';
import { useAuthStore } from '../../stores/authStore';
import styles from './WorkObjectSearchPage.module.css';

const { Paragraph, Title } = Typography;
const EMPTY_ITEMS: WorkObjectListResponseItemsItem[] = [];

function workObjectTitle(item: WorkObjectListResponseItemsItem) {
  return item.state_authority === 'internal' ? item.title : item.source_title;
}

function matchedFields(
  item: WorkObjectListResponseItemsItem,
  term: string,
): string[] {
  if (term.length === 0) {
    return [];
  }
  const normalizedTerm = normalizeSearchValue(term);
  const fields: string[] = [];
  const title = workObjectTitle(item);
  if (
    title != null &&
    normalizeSearchValue(title).includes(normalizedTerm)
  ) {
    fields.push('标题');
  }
  if (
    item.source_ref !== null &&
    normalizeSearchValue(item.source_ref) === normalizedTerm
  ) {
    fields.push('来源编号');
  }
  if (
    item.assignee_display_name !== null &&
    normalizeSearchValue(item.assignee_display_name) === normalizedTerm
  ) {
    fields.push('责任人');
  }
  return fields;
}

function newestFetchedAt(
  items: readonly WorkObjectListResponseItemsItem[],
): string | null {
  let newest: string | null = null;
  for (const item of items) {
    if (item.source_fetched_at === null) {
      continue;
    }
    if (
      newest === null ||
      new Date(item.source_fetched_at).getTime() > new Date(newest).getTime()
    ) {
      newest = item.source_fetched_at;
    }
  }
  return newest;
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? `${error.code}: ${lifecycleErrorText(error)}` : '请求失败，请重新读取核对。';
}

function SearchPageContextRegistration({
  declaration,
}: {
  declaration: PageContextDeclaration;
}) {
  usePageContextRegistration(declaration);
  return null;
}

export default function WorkObjectSearchPage() {
  const authGeneration = useAuthStore((state) => state.generation);
  return <SearchContent key={authGeneration} authGeneration={authGeneration} />;
}

function SearchContent({ authGeneration }: { authGeneration: number }) {
  const [searchParams] = useSearchParams();
  const client = useQueryClient();
  const [blocked, setBlocked] = useState(false);
  const [scope, setScope] = useState<'current' | 'completed' | 'withdrawn'>('current');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [range, setRange] = useState<{ from: string; to: string } | null>(null);
  const [rangeError, setRangeError] = useState('');
  const rawTerm = searchParams.get('q');
  const term = normalizeSearchQuery(rawTerm) ?? '';
  const hasSearch = rawTerm !== null && term.length > 0;

  const listQuery = useInfiniteQuery({
    queryKey: ['work-objects', authGeneration, 'search', 'all', term, scope, range],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => readWorkQueue({ q: term, oa_view: 'all', ...(scope === 'current' ? {} : { completion: scope,
      ...(range ? { history_from: range.from, history_to: range.to } : {}) }), ...(pageParam ? { cursor: pageParam } : {}) }),
    enabled: hasSearch && !blocked && (scope === 'current' || range !== null),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  useEffect(() => {
    if (!accessLost(listQuery.error)) return;
    setBlocked(true); logDispatchFailure('work_object_search', listQuery.error);
    useAIDockStore.getState().clearPageContext('work-object-search');
    client.removeQueries({ queryKey: ['work-objects', authGeneration, 'search'] });
  }, [listQuery.error, authGeneration, client]);
  useEffect(() => { setBlocked(false); }, [term]);
  const results = useMemo(() => listQuery.isSuccess && !blocked ? listQuery.data.pages.flatMap((page) => page.items) : EMPTY_ITEMS, [listQuery.data, listQuery.isSuccess, blocked]);
  const applyRange = () => {
    if (!from || !to || from > to) { setRangeError('请选择有效起止日期。'); return; }
    const start = new Date(`${from}T00:00:00`); const end = new Date(`${to}T00:00:00`); end.setDate(end.getDate() + 1);
    setRange({ from: start.toISOString(), to: end.toISOString() }); setRangeError(''); setBlocked(false);
  };
  const resultFreshness = newestFetchedAt(results);

  const pageContextDeclaration = useMemo<PageContextDeclaration>(() => {
    const allowedCapabilities = [
      ...new Set(
        results.flatMap((item) =>
          item.handling_capability_id === null
            ? []
            : [item.handling_capability_id],
        ),
      ),
    ];
    return {
      surface_id: 'work-object-search',
      organization_scope: null,
      work_object_refs: results.slice(0, 200).map((item) => ({
        work_object_id: item.work_object_id,
      })),
      source_refs: results.slice(0, 200).flatMap((item) =>
        item.source_ref === null
          ? []
          : [{ source_system: item.source_system, source_ref: item.source_ref }],
      ),
      filters: hasSearch
        ? [
            {
              field: 'query',
              operator: 'equals',
              value: term,
              source: 'visible_control',
            },
          ]
        : [],
      selected_metric: null,
      allowed_capabilities: allowedCapabilities,
      freshness:
        resultFreshness === null
          ? { state: 'unknown', observed_at: null }
          : { state: 'reported', observed_at: resultFreshness },
      visibility: 'principal',
    };
  }, [hasSearch, resultFreshness, results, term]);

  const scopeHeadline = !hasSearch
    ? '等待搜索'
    : scope !== 'current' && !range ? '请选择历史日期范围'
    : listQuery.isPending
      ? '正在查找'
      : listQuery.isError
        ? '查找失败'
        : `找到 ${results.length} 条`;
  const scopeText = !hasSearch
    ? '提交关键词后，将在你有权查看的全部工作事项中检索。'
    : listQuery.isPending
      ? '正在检索你有权查看的全部工作事项。'
      : listQuery.isError
        ? '工作事项搜索请求失败。'
        : '搜索范围：你有权查看的全部工作事项。';
  const emptyReason = !hasSearch
    ? '尚未开始搜索，因为还没有提交关键词。'
    : scope !== 'current' && !range ? '请选择历史起止日期后查询。'
    : listQuery.isPending
      ? '正在查找你有权查看的工作事项，请稍候。'
      : listQuery.isError
        ? `查找失败：${errorText(listQuery.error)}。`
        : '没有匹配项。已在你有权查看的全部工作事项中检索。';
  const emptyNextStep = !hasSearch
    ? '下一步：在顶部搜索框输入标题片段、完整来源编号或完整责任人。'
    : listQuery.isPending
      ? '下一步：请稍候，检索完成后会自动显示结果。'
      : listQuery.isError
        ? '下一步：稍后重试，或先回到工作事项页检查数据状态。'
        : '下一步：换个标题关键词，或输入完整的来源编号、责任人。';

  return (
    <Space className={styles.page} orientation="vertical" size="large">
      <Card className={styles.hero} styles={{ body: { padding: 28 } }}>
        <Flex align="center" justify="space-between" gap={24} wrap>
          <div>
            <Title className={styles.title} level={1}>工作事项搜索</Title>
            <Paragraph className={styles.copy}>
              标题可输入其中一段；来源编号和责任人请输入完整内容。
              本页显式检索全部 OA 历史状态；默认当前视图不包含“未再确认”记录。
            </Paragraph>
          </div>
          <div className={styles.scopeCard} aria-live="polite">
            <strong>{scopeHeadline}</strong>
            <span>{scopeText}</span>
          </div>
        </Flex>
      </Card>

      <div className={styles.historyFilters}><Select aria-label="搜索事项范围" value={scope} onChange={setScope} options={[
        { value: 'current', label: '当前工作集（含全部 OA 状态）' }, { value: 'completed', label: '内部办结历史' }, { value: 'withdrawn', label: '内部撤回历史' },
      ]} />
        {scope !== 'current' ? <><label>开始日期<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
          <label>结束日期<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label><Button onClick={applyRange}>查询历史区间</Button></> : null}
        <Button disabled={scope !== 'current' && !range} onClick={() => { setBlocked(false); void listQuery.refetch(); }}>重新读取搜索</Button>
      </div>
      {rangeError ? <p role="alert">{rangeError}</p> : null}
      {blocked ? <Alert type="warning" title="当前访问已失效，搜索内容和上下文引用已清除。" /> : null}

      {hasSearch && listQuery.isError ? (
        <Alert
          showIcon
          type="error"
          title="工作事项搜索失败"
          description={`${errorText(listQuery.error)}；下一步：稍后重试，或先回到工作事项页检查数据状态。`}
        />
      ) : null}

      {hasSearch && listQuery.isSuccess && listQuery.data.pages.some((page) => page.limit_exceeded) ? (
        <Alert
          showIcon
          type="warning"
          title="结果过多，请缩小范围"
          description="当前仅展示前 200 条：有截止时间的优先，截止越早越靠前；截止时间相同或均未设置时，按首次入库时间从新到旧选取。请缩小搜索范围。"
        />
      ) : null}

      <section className={styles.results} aria-labelledby="search-results-title">
        <div className={styles.resultsHeader}>
          <div>
            <Title id="search-results-title" level={2}>搜索结果</Title>
            <p>{hasSearch ? `关键词：${term}` : '提交关键词后在这里显示命中项。'}</p>
          </div>
          <Button><Link to="/work-objects">返回工作事项</Link></Button>
        </div>

        {!listQuery.isSuccess || results.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={(
              <div className={styles.emptyState}>
                <strong>{emptyReason}</strong>
                <span>{emptyNextStep}</span>
              </div>
            )}
          />
        ) : (
          <div className={styles.resultList} role="list">
            {results.map((item) => (
              <div
                className={styles.resultItem}
                key={item.work_object_id}
                role="listitem"
              >
                <div className={styles.resultBody}>
                  <div className={styles.resultHeading}>
                    {workObjectTitle(item) == null ? <span>未提供标题</span> : <Link to={`/work-objects?work_object_id=${encodeURIComponent(item.work_object_id)}`}><strong>{workObjectTitle(item)}</strong></Link>}
                    {item.state_authority === 'external_snapshot' ? <Tag>{item.oa_observation.pending_state === 'unconfirmed'
                      ? '当前待办未再确认' : item.oa_observation.pending_state === 'legacy_unverified'
                        ? '历史快照，尚未重新核对' : '当前待办'}</Tag> : null}
                    <div className={styles.matchTags} aria-label="匹配字段">
                      {matchedFields(item, term).map((field) => (
                        <Tag color="blue" key={field}>命中{field}</Tag>
                      ))}
                    </div>
                  </div>
                  <div className={styles.resultMeta}>
                    {item.assignee_display_name === null ? <span>未提供显示名</span> : <span data-assignee-value>责任人：{item.assignee_display_name}</span>}
                    <span>
                      来源：{item.source_system.toUpperCase()}
                      {item.source_ref === null ? '' : ` · ${item.source_ref}`}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      {listQuery.hasNextPage && !blocked ? <Button loading={listQuery.isFetchingNextPage} onClick={() => void listQuery.fetchNextPage()}>加载更多搜索结果</Button> : null}
      {hasSearch && listQuery.isSuccess && !blocked ? (
        <SearchPageContextRegistration declaration={pageContextDeclaration} />
      ) : null}
    </Space>
  );
}
