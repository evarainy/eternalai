import { useState } from 'react';
import type { CSSProperties } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../api/mutator';
import {
  deleteMemoryApiV1MemoriesIdDelete as deleteMemory,
  editMemoryApiV1MemoriesIdPatch as editMemory,
  listMemoriesApiV1MemoriesGet as listMemories,
} from '../../generated/memories/memories';
import { UserMemoryCategory } from '../../generated/memories/memories.schemas';
import type { UserMemory } from '../../generated/memories/memories.schemas';
import { PageFrame } from '../../shared/ui/PageFrame';
import { useAuthStore } from '../../stores/authStore';
import styles from './MemoriesPage.module.css';

/** 与后端 `EditMemoryRequest.content` 的 maxLength 一致。 */
const CONTENT_LIMIT = 200;

const CATEGORY_LABELS: Record<UserMemoryCategory, string> = {
  answer_style: '回答偏好',
  frequent_topic: '常问的事',
  work_background: '工作背景',
};

/** 后端对修改内容只回一个统一的拒绝码，不区分是超长还是含敏感信息，这里照它的规则说清楚。 */
const INVALID_CONTENT_TEXT =
  '没有保存：内容不能为空、不超过 200 字，也不能含身份证号、银行卡号、电话、住址、密码或验证码。';

type Notice = { tone: 'ok' | 'error'; text: string };

function updatedLabel(value: string): string {
  return new Date(value).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function isGone(error: Error): boolean {
  return error instanceof ApiError && error.status === 404;
}

/**
 * 设置 › 个人记忆：AI 从本人对话里记下的回答偏好、常问的事和工作背景，只给本人看、改、删。
 * 换了登录身份就整块重建，不留上一个人的列表、编辑内容或提示。
 */
export default function MemoriesPage() {
  const generation = useAuthStore((state) => state.generation);
  return (
    <PageFrame title="个人记忆">
      <MemoryList generation={generation} key={generation} />
    </PageFrame>
  );
}

function MemoryList({ generation }: { generation: number }) {
  const queryClient = useQueryClient();
  const queryKey = ['memories', generation] as const;
  const list = useQuery({ queryKey, queryFn: listMemories });
  const [editing, setEditing] = useState<{ id: string; draft: string } | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey });
  const gone = () => {
    setNotice({ tone: 'error', text: '这条记忆已经不在了，列表已刷新。' });
    void refresh();
  };

  const save = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) => editMemory(id, { content }),
    onSuccess: () => {
      setEditing(null);
      setNotice({ tone: 'ok', text: '已保存。' });
      void refresh();
    },
    onError: (error) => {
      if (!isGone(error)) return;
      setEditing(null);
      gone();
    },
  });
  const remove = useMutation({
    mutationFn: deleteMemory,
    onSuccess: () => {
      setNotice({ tone: 'ok', text: '已删除。' });
      void refresh();
    },
    onError: (error) => {
      if (isGone(error)) gone();
      else setNotice({ tone: 'error', text: '暂时没能删除，请稍后再试。' });
    },
  });
  const busy = save.isPending || remove.isPending;
  const saveError = save.error === null || isGone(save.error)
    ? null
    : save.error instanceof ApiError && save.error.status === 422
      ? INVALID_CONTENT_TEXT
      : '暂时没能保存，请稍后再试。';

  const startEdit = (item: UserMemory) => {
    save.reset();
    setArmed(null);
    setNotice(null);
    setEditing({ id: item.id, draft: item.content });
  };

  const deleteClick = (id: string) => {
    if (armed !== id) {
      setArmed(id);
      return;
    }
    setArmed(null);
    setNotice(null);
    remove.mutate(id);
  };

  const renderItem = (item: UserMemory) => {
    if (editing?.id === item.id) {
      const draft = editing.draft;
      return (
        <li className={styles.item} key={item.id}>
          <div className={styles.editor} data-focus-ring="host">
            <textarea
              aria-label={`修改${CATEGORY_LABELS[item.category]}`}
              disabled={save.isPending}
              maxLength={CONTENT_LIMIT}
              onChange={(event) => setEditing({ id: item.id, draft: event.target.value })}
              rows={3}
              value={draft}
            />
          </div>
          {saveError === null ? null : (
            <p className={styles.note} data-tone="error" role="alert">{saveError}</p>
          )}
          <div className={styles.itemFoot}>
            <span className="wb-num">{`${draft.length}/${CONTENT_LIMIT}`}</span>
            <span className="wb-grow" />
            <button
              className="wb-btn wb-btn-quiet wb-btn-sm"
              disabled={save.isPending}
              onClick={() => { save.reset(); setEditing(null); }}
              type="button"
            >
              取消
            </button>
            <button
              className="wb-btn wb-btn-primary wb-btn-sm"
              disabled={busy || draft.trim() === '' || draft.trim() === item.content}
              onClick={() => save.mutate({ id: item.id, content: draft })}
              type="button"
            >
              {save.isPending ? '正在保存…' : '保存'}
            </button>
          </div>
        </li>
      );
    }
    return (
      <li className={styles.item} key={item.id}>
        <p className={styles.content}>{item.content}</p>
        <div className={styles.itemFoot}>
          <time dateTime={item.updated_at}>{`更新于 ${updatedLabel(item.updated_at)}`}</time>
          <span className="wb-grow" />
          <button
            className="wb-btn wb-btn-quiet wb-btn-sm"
            disabled={busy}
            onClick={() => startEdit(item)}
            type="button"
          >
            修改
          </button>
          <button
            className="wb-btn wb-btn-urge wb-btn-sm"
            data-sure={armed === item.id ? 'true' : 'false'}
            disabled={busy}
            onBlur={() => setArmed(null)}
            onClick={() => deleteClick(item.id)}
            type="button"
          >
            {remove.isPending && remove.variables === item.id
              ? '正在删除…'
              : armed === item.id ? '确认删除' : '删除'}
          </button>
        </div>
      </li>
    );
  };

  return (
    <div className={styles.page}>
      <p className={styles.intro}>
        AI 会从你的对话中自动记住回答偏好、常问的事和工作背景，只有你本人能看到。删除后，同一句话不会再被记住。
      </p>
      {notice === null ? null : (
        <p className={styles.note} data-tone={notice.tone} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </p>
      )}
      {list.isPending ? (
        <p className={styles.state} role="status">正在读取…</p>
      ) : list.isError ? (
        <p className={styles.note} data-tone="error" role="alert">
          暂时读不到个人记忆。
          <button disabled={list.isFetching} onClick={() => { void list.refetch(); }} type="button">
            {list.isFetching ? '正在重试…' : '重试'}
          </button>
        </p>
      ) : list.data.items.length === 0 ? (
        <p className={styles.state}>还没有记住任何内容</p>
      ) : (
        Object.values(UserMemoryCategory).map((category, index) => {
          const items = list.data.items.filter((item) => item.category === category);
          return items.length === 0 ? null : (
            <section
              aria-label={CATEGORY_LABELS[category]}
              className={styles.group}
              key={category}
              style={{ '--i': index } as CSSProperties}
            >
              <h2 className={styles.groupTitle}>
                {CATEGORY_LABELS[category]}
                <span className="wb-num">{items.length}</span>
              </h2>
              <ul className={styles.items}>{items.map(renderItem)}</ul>
            </section>
          );
        })
      )}
    </div>
  );
}
