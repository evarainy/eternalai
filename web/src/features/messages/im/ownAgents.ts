import { useEffect, useState } from 'react';
import { readPersonalAgents } from '../personalAgentApi';
import { onImReset } from '../zulip/imStore';

/**
 * 本人名下 AI 助手的 bot 用户 id（V3 入口：在与本人助手的会话里打开 PersonalAgentPanel）。每个身份成功读到
 * 一次就缓存，换身份随 resetIm 作废。读取失败不缓存、不当成「没有助手」，由调用方提供重试。
 */
let cached: Promise<Set<number>> | null = null;
onImReset(() => {
  cached = null;
});

function load(): Promise<Set<number>> {
  cached ??= readPersonalAgents().then((result) => new Set(result.agents.map((agent) => Number(agent.bot_user_id))));
  cached.catch(() => {
    cached = null;
  });
  return cached;
}

export function useOwnAgentBots(): { bots: Set<number>; failed: boolean; retry: () => void } {
  const [bots, setBots] = useState<Set<number>>(new Set());
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    load().then(
      (value) => {
        if (!live) return;
        setBots(value);
        setFailed(false);
      },
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [attempt]);
  return { bots, failed, retry: () => setAttempt((value) => value + 1) };
}
