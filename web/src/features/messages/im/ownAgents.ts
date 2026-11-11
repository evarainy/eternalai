import { useEffect, useState } from 'react';
import { readPersonalAgents } from '../personalAgentApi';
import { onImReset } from '../zulip/imStore';

/**
 * 本人名下 AI 助手的 bot 用户 id（V3 入口：在与本人助手的会话里打开 PersonalAgentPanel）。每个身份只读一次，
 * 换身份随 resetIm 作废；读不到（个人助手未开通等）就当作没有，不显示入口。
 */
let cached: Promise<Set<number>> | null = null;
onImReset(() => {
  cached = null;
});

function load(): Promise<Set<number>> {
  cached ??= readPersonalAgents().then(
    (result) => new Set(result.agents.map((agent) => Number(agent.bot_user_id))),
    () => new Set<number>(),
  );
  return cached;
}

export function useOwnAgentBots(): Set<number> {
  const [bots, setBots] = useState<Set<number>>(new Set());
  useEffect(() => {
    let live = true;
    void load().then((value) => {
      if (live) setBots(value);
    });
    return () => {
      live = false;
    };
  }, []);
  return bots;
}
