import message from 'antd/es/message';
import { getUserPreferences, putUserPreferences } from '../api/userPreferences';
import type { UserPreferencesDocument } from '../api/userPreferences';
import { adoptHomeLayout, useHomeLayoutStore } from '../features/desk/homeLayout';
import { adoptAppearance, appearanceChoices, useAppearanceStore } from '../stores/appearanceStore';
import { useAuthStore } from '../stores/authStore';

/** 连续操作（拖动、连点皮肤）合并成一次保存。 */
const SAVE_DELAY_MS = 600;

function snapshot(): UserPreferencesDocument {
  return {
    appearance: { ...appearanceChoices(useAppearanceStore.getState()) },
    home_layout: { columns: useHomeLayoutStore.getState().columns },
  };
}

/**
 * 一次登录身份（一个 `generation`）的同步：先读服务器上本人的设置并采用，之后把修改整份存回去。
 *
 * - 读成功前不保存：读不到就不写，免得本浏览器的缓存覆盖服务器上的真实设置。
 * - 同时最多一个保存请求；在途期间的新修改等它回来后再比较、再发，保证最后落库的是最后的选择。
 * - 身份一变（退出、换人），这次同步整体作废：定时器清掉，在途请求回来后什么都不做，
 *   上一个人没存完的修改不会写进下一个人的记录。
 */
function startSession(generation: number): () => void {
  let live = true;
  let unsubscribe: (() => void) | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let saved = '';
  let saving = false;
  let again = false;

  const current = () => {
    const auth = useAuthStore.getState();
    return live && auth.status === 'authenticated' && auth.generation === generation;
  };

  const flush = async () => {
    if (!current()) return;
    if (saving) {
      again = true;
      return;
    }
    const document = snapshot();
    const body = JSON.stringify(document);
    if (body === saved) return;
    saving = true;
    try {
      await putUserPreferences(document);
      if (!current()) return;
      saved = body;
    } catch {
      if (!current()) return;
      void message.error('外观或首页摆法没能保存，请稍后再改一次。');
    } finally {
      saving = false;
      if (current() && again) {
        again = false;
        void flush();
      }
    }
  };

  const schedule = () => {
    if (!current()) return;
    clearTimeout(timer);
    timer = setTimeout(() => void flush(), SAVE_DELAY_MS);
  };

  void getUserPreferences().then(
    (document) => {
      if (!current()) return;
      adoptAppearance(document.appearance);
      adoptHomeLayout(document.home_layout);
      saved = JSON.stringify(snapshot());
      const offAppearance = useAppearanceStore.subscribe((state, previous) => {
        if (JSON.stringify(appearanceChoices(state)) !== JSON.stringify(appearanceChoices(previous))) {
          schedule();
        }
      });
      const offLayout = useHomeLayoutStore.subscribe((state, previous) => {
        if (state.columns !== previous.columns) schedule();
      });
      unsubscribe = () => {
        offAppearance();
        offLayout();
      };
    },
    () => {
      if (!current()) return;
      void message.warning('外观和首页摆法暂时没能从服务器读取，这次的修改不会保存。');
    },
  );

  return () => {
    live = false;
    clearTimeout(timer);
    unsubscribe?.();
  };
}

/** 应用启动时调用一次：跟着登录身份走，每换一次身份重新读一次本人的设置。 */
export function startUserPreferencesSync(): void {
  let stop: (() => void) | undefined;
  let active: number | undefined;
  const follow = () => {
    const { status, generation } = useAuthStore.getState();
    const next = status === 'authenticated' ? generation : undefined;
    if (next === active) return;
    stop?.();
    stop = undefined;
    active = next;
    if (next !== undefined) stop = startSession(next);
  };
  follow();
  useAuthStore.subscribe(follow);
}
