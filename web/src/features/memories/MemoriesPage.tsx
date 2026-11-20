import { useState } from 'react';
import type { CSSProperties } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, MEMORY_INPUT_INVALID_CODE } from '../../api/mutator';
import {
  deleteMemoryApiV1MemoriesIdDelete as deleteMemory,
  editMemoryApiV1MemoriesIdPatch as editMemory,
  listMemoriesApiV1MemoriesGet as listMemories,
} from '../../generated/memories/memories';
import { UserMemoryCategory } from '../../generated/memories/memories.schemas';
import type {
  MemoryInputErrorDetailReason,
  UserMemory,
} from '../../generated/memories/memories.schemas';
import { useIdentityUnconfirmed } from '../../app/identity';
import { PageFrame } from '../../shared/ui/PageFrame';
import { useAuthStore } from '../../stores/authStore';
import styles from './MemoriesPage.module.css';

/** 与后端 `EditMemoryRequest.content` 一致：去掉首尾空白后按字符（码点）计，最多 200。 */
const CONTENT_LIMIT = 200;

const CATEGORY_LABELS: Record<UserMemoryCategory, string> = {
  answer_style: '回答偏好',
  frequent_topic: '常问的事',
  work_background: '工作背景',
};

/** 修改被拒（422）时后端给出的原因。 */
const INPUT_REASON_TEXT: Record<MemoryInputErrorDetailReason, string> = {
  empty: '内容不能为空',
  too_long: '内容太长，请控制在 200 字以内',
  sensitive: '这条内容像是含有证件号、电话、密码等敏感信息，不能保存',
};

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
 * 身份复核期间先收起列表并停止查询（编辑内容保留，同一个人确认后接着改）；换了登录身份就按新代次整块重建，
 * 不留上一个人的列表、编辑内容或提示。切回标签页不自动重取，等身份确认后再取。
 */
export default function MemoriesPage() {
  const generation = useAuthStore((state) => state.generation);
  const unconfirmed = useIdentityUnconfirmed();
  return (
    <PageFrame title="个人记忆">
      {unconfirmed ? <p className={styles.state} role="status">确认登录身份后再显示个人记忆。</p> : null}
      <MemoryList generation={generation} key={generation} paused={unconfirmed} />
    </PageFrame>
  );
}

function MemoryList({ generation, paused }: { generation: number; paused: boolean }) {
  const queryClient = useQueryClient();
  const queryKey = ['memories', generation] as const;
  const list = useQuery({
    queryKey, queryFn: listMemories, enabled: !paused, refetchOnWindowFocus: false,
  });
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
    : save.error instanceof ApiError && save.error.code === MEMORY_INPUT_INVALID_CODE
      ? INPUT_REASON_TEXT[save.error.reason as MemoryInputErrorDetailReason]
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
      const content = draft.trim();
      const length = Array.from(content).length;
      return (
        <li className={styles.item} key={item.id}>
          <div className={styles.editor} data-focus-ring="host">
            <textarea
              aria-label={`修改${CATEGORY_LABELS[item.category]}`}
              disabled={save.isPending}
              onChange={(event) => setEditing({ id: item.id, draft: event.target.value })}
              rows={3}
              value={draft}
            />
          </div>
          {saveError === null ? null : (
            <p className={styles.note} data-tone="error" role="alert">{saveError}</p>
          )}
          <div className={styles.itemFoot}>
            <span className="wb-num">{`${length}/${CONTENT_LIMIT}`}</span>
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
              disabled={busy || length === 0 || length > CONTENT_LIMIT || content === item.content}
              onClick={() => save.mutate({ id: item.id, content })}
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
    <div className={styles.page} hidden={paused}>
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
