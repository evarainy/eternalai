import { useImStore } from '../zulip/imStore';
import { unreadTotal } from './viewModel';
import { useImView } from './viewStore';
import styles from './ConversationList.module.css';

/** 左栏顶部的「消息 / 联系人」切换（FE_PLAN §3）。消息页签带未读总数，静音的不计入。 */
export function ListTabs() {
  const tab = useImView((view) => view.tab);
  const total = useImStore(unreadTotal);
  return (
    <div className={styles.top} role="tablist" aria-label="消息与联系人">
      <button
        aria-selected={tab === 'messages'}
        className={styles.topTab}
        onClick={() => useImView.getState().showMessages()}
        role="tab"
        type="button"
      >
        消息{total > 0 ? <span className={styles.topCount}>{total > 99 ? '99+' : total}</span> : null}
      </button>
      <button
        aria-selected={tab === 'contacts'}
        className={styles.topTab}
        onClick={() => useImView.getState().showContact(useImView.getState().contact)}
        role="tab"
        type="button"
      >
        联系人
      </button>
    </div>
  );
}
