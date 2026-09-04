import { useState } from 'react';
import { Button, Input, Modal } from 'antd';
import { Icon } from '../../shared/ui/Icon';
import {
  BINDING_CHOICES,
  BINDING_CHOICE_LABELS,
  OPEN_MODE_LABELS,
  RISK_CHOICES,
  RISK_CHOICE_LABELS,
  SOFTWARE_SOURCES,
  dedupeVisibleTo,
  loadNewSoftwareDraft,
  saveNewSoftwareDraft,
} from './newSoftwareDraft';
import type { NewSoftwareDraft } from './newSoftwareDraft';
import styles from './AppsPage.module.css';

/**
 * 「新建应用」弹窗，形态照定稿画板 `_scratch/design/glass/AppNew.dc.html`。
 *
 * 2026-09-02 裁决：界面先行、后端不做。所以这里**不新增任何 API**——「存草稿」落本机
 * `localStorage`，「提交审核」在审核端点接进来之前不可用；界面上写明「提交审核后才对他人可见」，
 * 不让用户以为建完就上线了。
 *
 * 「嵌在工作台里面」默认禁用并写明原因：OA 实测响应带 `X-Frame-Options: SAMEORIGIN`，嵌不进来。
 */

const SAVE_NOTICE = '草稿存在这台电脑上，换台电脑就没有了。';
const SAVE_FAILED_NOTICE = '浏览器不让存东西，草稿没存上。先把要点抄到别处。';
const NAME_REQUIRED_NOTICE = '还没有填名字。先把名字填上。';

export interface NewSoftwareDialogProps {
  onClose: () => void;
  open: boolean;
}

export function NewSoftwareDialog({ onClose, open }: NewSoftwareDialogProps) {
  const [draft, setDraft] = useState<NewSoftwareDraft>(() => loadNewSoftwareDraft());
  const [visibleToInput, setVisibleToInput] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  const update = <Key extends keyof NewSoftwareDraft>(
    key: Key,
    value: NewSoftwareDraft[Key],
  ) => {
    setNotice(null);
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const addVisibleTo = () => {
    const added = visibleToInput.trim();
    if (added.length === 0) {
      return;
    }
    setVisibleToInput('');
    update('visibleTo', dedupeVisibleTo([...draft.visibleTo, added]));
  };

  const store = () => {
    if (draft.name.trim().length === 0) {
      setNotice(NAME_REQUIRED_NOTICE);
      return;
    }
    setNotice(saveNewSoftwareDraft(draft) ? SAVE_NOTICE : SAVE_FAILED_NOTICE);
  };

  return (
    <Modal
      className={styles.dialog}
      footer={null}
      onCancel={onClose}
      open={open}
      title="新建应用"
      width={768}
    >
      <p className={styles.caption}>先存草稿，提交审核通过后别人才看得到。</p>

      <fieldset className={styles.optionSet}>
        <legend className={styles.fieldLabel}>这个软件是哪儿来的？</legend>
        <div className={styles.optionGrid} role="radiogroup" aria-label="这个软件是哪儿来的？">
          {SOFTWARE_SOURCES.map((entry) => (
            <button
              aria-checked={draft.source === entry.value}
              className={
                draft.source === entry.value ? styles.optionOn : styles.optionOff
              }
              key={entry.value}
              onClick={() => update('source', entry.value)}
              role="radio"
              type="button"
            >
              <span className={styles.optionTitle}>{entry.title}</span>
              <span className={styles.optionHint}>{entry.hint}</span>
            </button>
          ))}
        </div>
      </fieldset>

      <div className={styles.dialogGrid}>
        <div className={styles.field}>
          <label htmlFor="new-software-name">叫什么名字</label>
          <Input
            id="new-software-name"
            onChange={(event) => update('name', event.target.value)}
            value={draft.name}
          />
        </div>
        <div className={styles.field}>
          <label htmlFor="new-software-summary">一句话说明是干什么的</label>
          <Input
            id="new-software-summary"
            onChange={(event) => update('summary', event.target.value)}
            value={draft.summary}
          />
        </div>
        <div className={styles.field}>
          <label htmlFor="new-software-address">访问地址</label>
          <Input
            id="new-software-address"
            onChange={(event) => update('address', event.target.value)}
            value={draft.address}
          />
        </div>
        <div className={styles.field}>
          <label htmlFor="new-software-owner">归哪个科室管 / 找谁</label>
          <Input
            id="new-software-owner"
            onChange={(event) => update('owner', event.target.value)}
            value={draft.owner}
          />
        </div>

        <div className={`${styles.field} ${styles.fieldWide}`}>
          <span className={styles.fieldLabel} id="new-software-open-label">
            点开以后怎么显示
          </span>
          <div
            aria-labelledby="new-software-open-label"
            className={styles.choiceRow}
            role="radiogroup"
          >
            <button
              aria-checked
              className={styles.choiceOn}
              role="radio"
              type="button"
            >
              {OPEN_MODE_LABELS.new_window}
            </button>
            <button
              aria-checked={false}
              className={styles.choiceOff}
              disabled
              role="radio"
              type="button"
            >
              {OPEN_MODE_LABELS.embedded}
            </button>
          </div>
          <p className={styles.caption}>
            实测 OA 不允许被别的页面嵌进去（响应头 X-Frame-Options: SAMEORIGIN），所以只能开新窗口。开新窗口不会关掉工作台这一页。
          </p>
        </div>

        <div className={styles.field}>
          <span className={styles.fieldLabel} id="new-software-binding-label">
            用之前要不要先绑这个系统的账号
          </span>
          <div
            aria-labelledby="new-software-binding-label"
            className={styles.choiceRow}
            role="radiogroup"
          >
            {BINDING_CHOICES.map((choice) => (
              <button
                aria-checked={draft.binding === choice}
                className={draft.binding === choice ? styles.choiceOn : styles.choiceOff}
                key={choice}
                onClick={() => update('binding', choice)}
                role="radio"
                type="button"
              >
                {BINDING_CHOICE_LABELS[choice]}
              </button>
            ))}
          </div>
          <p className={styles.caption}>选「要绑」，别人第一次点开会先让他绑账号。</p>
        </div>

        <div className={styles.field}>
          <span className={styles.fieldLabel} id="new-software-risk-label">
            这个系统会不会改数据
          </span>
          <div
            aria-labelledby="new-software-risk-label"
            className={styles.choiceRow}
            role="radiogroup"
          >
            {RISK_CHOICES.map((choice) => (
              <button
                aria-checked={draft.risk === choice}
                className={draft.risk === choice ? styles.choiceOn : styles.choiceOff}
                key={choice}
                onClick={() => update('risk', choice)}
                role="radio"
                type="button"
              >
                {RISK_CHOICE_LABELS[choice]}
              </button>
            ))}
          </div>
          <p className={styles.caption}>会改数据的，AI 不会自己动手，一定先问你。</p>
        </div>

        <div className={`${styles.field} ${styles.fieldWide}`}>
          <label htmlFor="new-software-visible">谁能在软件中心看见它</label>
          <div className={styles.chipWell}>
            {draft.visibleTo.map((scope) => (
              <span className={styles.chip} key={scope}>
                {scope}
                <button
                  aria-label={`删除可见范围 ${scope}`}
                  className={styles.chipRemove}
                  onClick={() =>
                    update(
                      'visibleTo',
                      draft.visibleTo.filter((item) => item !== scope),
                    )
                  }
                  type="button"
                >
                  <Icon name="close" size={14} strokeWidth={2.3} />
                </button>
              </span>
            ))}
            <Input
              className={styles.chipInput}
              id="new-software-visible"
              onChange={(event) => setVisibleToInput(event.target.value)}
              onPressEnter={addVisibleTo}
              placeholder="输入一个科室"
              value={visibleToInput}
              variant="borderless"
            />
            <Button className={styles.chipAdd} onClick={addVisibleTo}>
              <Icon name="plus" size={15} strokeWidth={2.2} />
              添加
            </Button>
          </div>
          <p className={styles.caption}>没选到的科室，在这一页看不到它。</p>
        </div>
      </div>

      <div className={styles.dialogWarn}>
        <span className={styles.warnMark}>
          <Icon name="alert" size={18} strokeWidth={1.9} />
        </span>
        <div>
          <b className={styles.warnTitle}>建好先是草稿，只有你自己看得见</b>
          <p className={styles.warnCopy}>
            提交审核后才对他人可见。审核功能还没有接进来，现在只能存草稿。
          </p>
        </div>
      </div>

      <div className={styles.dialogFooter}>
        <span className={styles.caption}>
          存草稿只存在这台电脑上，也不会去试这个地址通不通。
        </span>
        <div className={styles.dialogActions}>
          <Button onClick={onClose} type="text">
            取消
          </Button>
          <Button onClick={store}>存草稿</Button>
          <Button disabled type="primary">
            提交审核
          </Button>
        </div>
      </div>
      {notice === null ? null : (
        <p className={styles.dialogNotice} role="status">
          {notice}
        </p>
      )}
    </Modal>
  );
}

export default NewSoftwareDialog;
