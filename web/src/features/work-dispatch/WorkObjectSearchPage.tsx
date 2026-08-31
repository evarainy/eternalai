import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Empty, Flex, Space, Tag, Typography } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError } from '../../api/mutator';
import { usePageContextRegistration } from '../../app/usePageContextRegistration';
import type { PageContextDeclaration } from '../../contracts/pageContext';
import { listWorkObjectsApiV1WorkObjectsGet as listWorkObjects } from '../../generated/work-objects/work-objects';
import type {
  WorkObjectListResponseItemsItem,
} from '../../generated/work-objects/work-objects.schemas';
import { useAuthStore } from '../../stores/authStore';
import styles from './WorkObjectSearchPage.module.css';

const { Paragraph, Text, Title } = Typography;
const EMPTY_ITEMS: WorkObjectListResponseItemsItem[] = [];

function normalizedEqualityValue(value: string): string {
  return value.trim().toLocaleLowerCase('zh-CN');
}

function matchedFields(
  item: WorkObjectListResponseItemsItem,
  term: string,
): string[] {
  if (term.length === 0) {
    return [];
  }
  const normalizedTerm = normalizedEqualityValue(term);
  const fields: string[] = [];
  if (
    item.source_title !== null &&
    item.source_title.toLocaleLowerCase('zh-CN').includes(normalizedTerm)
  ) {
    fields.push('标题');
  }
  if (
    item.source_ref !== null &&
    normalizedEqualityValue(item.source_ref) === normalizedTerm
  ) {
    fields.push('来源编号');
  }
  if (normalizedEqualityValue(item.assignee_display_name) === normalizedTerm) {
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
  if (error instanceof ApiError) {
    return `${error.code}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'unknown_error: 请求失败';
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
  const [searchParams] = useSearchParams();
  const authGeneration = useAuthStore((state) => state.generation);
  const rawTerm = searchParams.get('q');
  const term = rawTerm?.trim() ?? '';
  const hasSearch = rawTerm !== null && term.length > 0;

  const listQuery = useQuery({
    queryKey: ['work-objects', authGeneration],
    queryFn: listWorkObjects,
  });
  const items = listQuery.isSuccess ? listQuery.data.items : EMPTY_ITEMS;
  const results = useMemo(
    () =>
      hasSearch
        ? items.filter((item) => matchedFields(item, term).length > 0)
        : EMPTY_ITEMS,
    [hasSearch, items, term],
  );
  const loadedCount = items.length;
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
      work_object_refs: results.map((item) => ({
        work_object_id: item.work_object_id,
      })),
      source_refs: results.flatMap((item) =>
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

  const scopeHeadline = listQuery.isPending
    ? '正在查找'
    : listQuery.isError
      ? '查找失败'
      : hasSearch
        ? `找到 ${results.length} 条`
        : '等待搜索';
  const scopeText = listQuery.isPending
    ? '正在读取当前可见的工作事项批次。'
    : listQuery.isError
      ? '当前可见的工作事项批次读取失败。'
      : `当前搜索范围：已加载的 ${loadedCount} 条工作事项。`;
  const emptyReason = listQuery.isPending
    ? '正在查找当前可见的工作事项，请稍候。'
    : listQuery.isError
      ? `查找失败：${errorText(listQuery.error)}。`
      : hasSearch
        ? `没有匹配项。已在当前已加载的 ${loadedCount} 条工作事项中查找；这不代表未加载的事项中一定不存在。`
        : `尚未开始搜索，因为还没有提交关键词。${scopeText}`;
  const emptyNextStep = listQuery.isPending
    ? '下一步：请稍候，批次加载完成后会自动显示结果。'
    : listQuery.isError
      ? '下一步：稍后重试，或先回到工作事项页检查数据状态。'
      : hasSearch
        ? '下一步：检查标题关键词，或输入完整的来源编号、责任人；也可回到工作事项页刷新当前批次后再试。'
        : '下一步：在顶部搜索框输入标题片段、完整来源编号或完整责任人，然后点击“搜索”。';

  return (
    <Space className={styles.page} orientation="vertical" size="large">
      <Card className={styles.hero} styles={{ body: { padding: 28 } }}>
        <Flex align="center" justify="space-between" gap={24} wrap>
          <div>
            <Text className={styles.eyebrow}>只查工作事项，不搜索消息、材料或会话</Text>
            <Title className={styles.title} level={1}>工作事项搜索</Title>
            <Paragraph className={styles.copy}>
              标题可输入其中一段；来源编号和责任人请输入完整内容。
            </Paragraph>
          </div>
          <div className={styles.scopeCard} aria-live="polite">
            <strong>{scopeHeadline}</strong>
            <span>{scopeText}</span>
          </div>
        </Flex>
      </Card>

      {listQuery.isError ? (
        <Alert
          showIcon
          type="error"
          title="当前批次读取失败"
          description={`${errorText(listQuery.error)}；下一步：稍后重试，或先回到工作事项页检查数据状态。`}
        />
      ) : null}

      {listQuery.isSuccess && listQuery.data.limit_exceeded ? (
        <Alert
          showIcon
          type="warning"
          title={`搜索只覆盖当前已加载的 ${loadedCount} 条工作事项`}
          description="列表接口仍有未加载事项；本次无命中不能证明系统中的其他事项不存在。"
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
                    <strong>{item.source_title ?? '内部工作事项'}</strong>
                    <div className={styles.matchTags} aria-label="匹配字段">
                      {matchedFields(item, term).map((field) => (
                        <Tag color="blue" key={field}>命中{field}</Tag>
                      ))}
                    </div>
                  </div>
                  <div className={styles.resultMeta}>
                    <span>责任人：{item.assignee_display_name}</span>
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
      {listQuery.isSuccess ? (
        <SearchPageContextRegistration declaration={pageContextDeclaration} />
      ) : null}
    </Space>
  );
}
