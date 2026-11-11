import { useEffect, useState } from 'react';
import { Drawer, Select } from 'antd';
import { PersonalAgentPanel } from '../PersonalAgentPanel';
import { Icon } from '../../../shared/ui/Icon';
import { loadStreamTopics, setTopicFilter, useImStore } from '../zulip/imStore';
import { Avatar } from './Avatar';
import { ChatSearch } from './ChatSearch';
import { useComposerBridge } from './composerStore';
import { Composer } from './Composer';
import type { TopicSetting } from './Composer';
import { GroupDrawer } from './GroupDrawer';
import { useOwnAgentBots } from './ownAgents';
import { ProfileCard } from './ProfileCard';
import { MessageList } from './MessageList';
import { SelectionBar } from './SelectionBar';
import { useSelection } from './selectionStore';
import {
  PRESENCE_LABEL, conversationAvatar, conversationMemberCount, conversationTitle, streamTopicNames, topicLabel, userDept,
  userPresence, userTitle,
} from './viewModel';
import styles from './ChatPane.module.css';

/** 话题筛选里「全部话题」的取值；真实话题名不会是这个（含控制字符）。 */
const ALL_TOPICS = '\u0000all';

/** 聊天区：头部、消息记录、输入框。会话内搜索、加成员、群设置抽屉在 lane 2/4 接入。 */
export function ChatPane() {
  const state = useImStore();
  const key = state.selected;
  const [searching, setSearching] = useState(false);
  const [panel, setPanel] = useState<'group' | 'agent' | null>(null);
  const ownAgents = useOwnAgentBots();
  // 旧群的话题读取状态（按群记）；失败时可重试。
  const [topicStatus, setTopicStatus] = useState<Record<number, 'loading' | 'failed' | 'ready'>>({});
  const [topicRetry, setTopicRetry] = useState(0);

  // 换会话时关掉搜索、清掉输入框的回复/编辑请求。
  useEffect(() => {
    setSearching(false);
    setPanel(null);
    useComposerBridge.getState().reset();
    if (useSelection.getState().key !== key) useSelection.getState().clear();
  }, [key]);

  // Ctrl+F（ImWorkspace 只在焦点处于 IM 区时转发）与输入框工具栏的「聊天记录」。
  useEffect(() => {
    const open = () => setSearching(true);
    window.addEventListener('eternalai:im-search', open);
    return () => window.removeEventListener('eternalai:im-search', open);
  }, []);

  const selecting = useSelection((selection) => selection.key !== null && selection.key === key);
  const conversation = key ? state.conversations[key] : undefined;
  const streamId = conversation?.kind === 'stream' ? conversation.streamId : null;
  const topicsPolicy = streamId === null ? undefined : state.subscriptions[streamId]?.topics_policy;
  const topics = conversation ? streamTopicNames(state, conversation) : [];
  const multiTopic = topics.length > 0;
  const filterTopic = key && state.topicFilter?.key === key ? state.topicFilter.topic : null;
  const latestTopic = (() => {
    const messages = key ? state.windows[key]?.messages ?? [] : [];
    const last = messages[messages.length - 1];
    return last?.type === 'stream' ? last.subject : '';
  })();

  // 旧群（不是「只用空话题」）打开时读一次话题。读到之前和读取失败时，发送必须明确选定话题。
  const legacy = streamId !== null && topicsPolicy !== 'empty_topic_only';
  useEffect(() => {
    if (streamId === null || !legacy) return;
    let live = true;
    setTopicStatus((current) => ({ ...current, [streamId]: 'loading' }));
    loadStreamTopics(streamId).then(
      () => live && setTopicStatus((current) => ({ ...current, [streamId]: 'ready' })),
      () => live && setTopicStatus((current) => ({ ...current, [streamId]: 'failed' })),
    );
    return () => {
      live = false;
    };
  }, [streamId, legacy, topicRetry]);
  const loadStatus = streamId === null ? 'ready' : topicStatus[streamId] ?? 'loading';
  // 需要选话题：多话题群；话题还没确定的旧群；本群明确禁止空话题。
  const needTopic = legacy && (multiTopic || loadStatus !== 'ready' || topicsPolicy === 'disable_empty_topic');
  let topicNote = null;
  if (needTopic && loadStatus === 'loading') topicNote = '正在读取本群话题…';
  if (needTopic && loadStatus === 'failed') {
    topicNote = (
      <>本群话题读取失败，请填写话题<button onClick={() => setTopicRetry((value) => value + 1)} type="button">重试</button></>
    );
  }
  const topicSetting: TopicSetting | undefined = needTopic
    ? { names: topics.filter((topic) => topic !== ''), filter: filterTopic, latest: latestTopic, note: topicNote }
    : undefined;
  if (!key || !conversation) {
    return <div className={styles.empty}>{state.phase === 'ready' ? '选择左侧的会话开始聊天' : ''}</div>;
  }

  const title = conversationTitle(state, conversation);
  const avatar = conversationAvatar(state, conversation);
  const single = avatar.kind === 'person' ? avatar : null;
  let sub;
  if (single && !single.bot) {
    const presence = userPresence(state, single.userId);
    const org = [userDept(state, single.userId), userTitle(state, single.userId)].filter(Boolean).join(' · ');
    sub = (
      <div className={styles.sub}>
        <span className={styles.dot} data-presence={presence} />
        {PRESENCE_LABEL[presence]}
        {org ? <><span className={styles.divider} />{org}</> : null}
      </div>
    );
  } else {
    const count = conversationMemberCount(state, conversation);
    sub = count === null ? null : <div className={styles.sub}>{count} 人</div>;
  }

  // 与本人 AI 助手的单聊：「更多」打开现有的个人助手面板（V3 入口，FE_PLAN §6 lane 4）。
  const agentChat = single?.bot === true && ownAgents.bots.has(single.userId);
  // 助手列表读取失败时，机器人单聊的「更多」用来重试，不把失败当成「没有本人助手」。
  const agentRetry = single?.bot === true && ownAgents.failed;
  const identity = (
    <>
      <Avatar avatar={avatar} />
      <div className={styles.titleBlock}>
        <h2 className={styles.title}>{title}{multiTopic ? <span className={styles.tag}>多话题</span> : null}</h2>
        {sub}
      </div>
    </>
  );

  return (
    <>
      <header className={styles.head}>
        {single && !single.bot
          ? <ProfileCard userId={single.userId}><button className={styles.identity} type="button">{identity}</button></ProfileCard>
          : identity}
        <div className={styles.icons}>
          {multiTopic ? (
            <Select
              aria-label="按话题筛选"
              className={styles.topicFilter}
              onChange={(value: string) => setTopicFilter(key, value === ALL_TOPICS ? null : value)}
              options={[
                { value: ALL_TOPICS, label: '全部话题' },
                ...topics.map((topic) => ({ value: topic, label: topicLabel(topic) })),
              ]}
              popupMatchSelectWidth={false}
              size="small"
              value={filterTopic ?? ALL_TOPICS}
              variant="filled"
            />
          ) : null}
          <button aria-label="在会话内搜索" className={styles.iconButton} onClick={() => setSearching(true)} title="在会话内搜索（Ctrl+F）" type="button">
            <Icon name="search" size={19} />
          </button>
          <button
            aria-label="添加成员"
            className={styles.iconButton}
            disabled={streamId === null}
            onClick={() => setPanel('group')}
            title={streamId === null ? '单聊不能直接加人，可新建群' : '添加成员'}
            type="button"
          >
            <Icon name="useradd" size={19} />
          </button>
          <button
            aria-label={streamId !== null ? '群设置' : agentChat ? '个人助手设置' : '更多'}
            className={styles.iconButton}
            disabled={streamId === null && !agentChat && !agentRetry}
            onClick={() => {
              if (streamId === null && agentRetry) ownAgents.retry();
              else setPanel(streamId !== null ? 'group' : 'agent');
            }}
            title={streamId !== null ? '群设置' : agentChat ? '个人助手资料与授权' : agentRetry ? '本人助手列表读取失败，点此重试' : '更多'}
            type="button"
          >
            <Icon name="more" size={20} strokeWidth={2.2} />
          </button>
        </div>
      </header>
      <div className={styles.messageArea}>
        {searching ? <ChatSearch conversationKey={key} onClose={() => setSearching(false)} /> : null}
        <MessageList conversationKey={key} messageWindow={state.windows[key]} state={state} />
      </div>
      {selecting
        ? <SelectionBar conversationKey={key} />
        : (
          <Composer
            conversationKey={key}
            enterSends={state.enterSends}
            key={key}
            placeholder={`发给 ${title}`}
            topic={topicSetting}
          />
        )}
      {panel === 'group' && streamId !== null
        ? <GroupDrawer conversationKey={key} onClose={() => setPanel(null)} streamId={streamId} />
        : null}
      <Drawer destroyOnHidden onClose={() => setPanel(null)} open={panel === 'agent'} size="large" title="个人助手">
        <PersonalAgentPanel />
      </Drawer>
    </>
  );
}
