import message from 'antd/es/message';
import { getUserPreferences, putUserPreferences } from '../api/userPreferences';
import type { UserPreferencesDocument } from '../api/userPreferences';
import { readMeApiV1MeGet } from '../generated/me/me';
import { useAuthStore } from '../stores/authStore';

/** 连续操作（拖动、连点皮肤）合并成一次保存。 */
const SAVE_DELAY_MS = 600;

/**
 * 两个 store 带浏览器本地缓存，加载时就会读存储；启动入口（`main.tsx`）在首屏前不读任何存储，
 * 所以等到第一次登录才加载它们。应用本身（`App.tsx`）早已静态引入，这里拿到的是同一份模块。
 */
type Stores = [typeof import('../stores/appearanceStore'), typeof import('../features/desk/homeLayout')];
let stores: Promise<Stores> | undefined;
function loadStores(): Promise<Stores> {
  stores ??= Promise.all([import('../stores/appearanceStore'), import('../features/desk/homeLayout')]);
  return stores;
}

function snapshot([appearance, layout]: Stores): UserPreferencesDocument {
  return {
    appearance: { ...appearance.appearanceChoices(appearance.useAppearanceStore.getState()) },
    home_layout: { columns: layout.useHomeLayoutStore.getState().columns },
  };
}

/**
 * 一次登录身份（一个 `generation`）的同步：先读服务器上本人的设置并采用，之后把修改整份存回去。
 *
 * - 读成功前不保存：读不到就不写，免得本浏览器的缓存覆盖服务器上的真实设置。
 * - 同时最多一个保存请求；在途期间的新修改等它回来后再比较、再发，保证最后落库的是最后的选择。
 * - 身份一变（退出、换人），这次同步整体作废：定时器清掉，在途请求回来后什么都不做，
 *   上一个人没存完的修改不会写进下一个人的记录。
 * - cookie 是整个浏览器共用的：另一个标签页换了人，这一页的 generation 要等身份重检回来才变。所以每次保存前
 *   先问一次服务器「现在是谁」（`restore_scope`，服务端签发的账号别名），和读设置时的身份不一致就不发。
 */
function startSession(generation: number): () => void {
  let live = true;
  let loaded: Stores | undefined;
  let unsubscribe: (() => void) | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let saved = '';
  let owner: string | null = null;
  let saving = false;
  let again = false;

  const current = () => {
    const auth = useAuthStore.getState();
    return live && auth.status === 'authenticated' && auth.generation === generation;
  };

  const flush = async () => {
    if (!current() || loaded === undefined) return;
    if (saving) {
      again = true;
      return;
    }
    const document = snapshot(loaded);
    const body = JSON.stringify(document);
    if (body === saved) return;
    saving = true;
    try {
      const identity = await readMeApiV1MeGet();
      if (!current() || (identity.restore_scope ?? null) !== owner) return;
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

  void Promise.all([loadStores(), readMeApiV1MeGet(), getUserPreferences()]).then(
    ([modules, identity, document]) => {
      if (!current()) return;
      const [appearance, layout] = modules;
      loaded = modules;
      owner = identity.restore_scope ?? null;
      appearance.adoptAppearance(document.appearance);
      layout.adoptHomeLayout(document.home_layout);
      saved = JSON.stringify(snapshot(modules));
      const { appearanceChoices } = appearance;
      const offAppearance = appearance.useAppearanceStore.subscribe((state, previous) => {
        if (JSON.stringify(appearanceChoices(state)) !== JSON.stringify(appearanceChoices(previous))) {
          schedule();
        }
      });
      const offLayout = layout.useHomeLayoutStore.subscribe((state, previous) => {
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
