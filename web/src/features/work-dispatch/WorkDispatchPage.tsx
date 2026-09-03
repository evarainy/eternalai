import { useState } from 'react';
import { Button, Input, Select } from 'antd';
import { Icon } from '../../shared/ui/Icon';
import {
  DISPATCH_KINDS,
  REMINDER_CHOICES,
  dedupeTargets,
  loadDraft,
  saveDraft,
} from './dispatchDraft';
import type { DispatchDraft, DispatchKind, ReminderChoice } from './dispatchDraft';
import styles from './WorkDispatchPage.module.css';

/**
 * 任务交办页，形态照定稿画板 `_scratch/design/glass/Dispatch.dc.html`。
 *
 * 两条边界必须一起读，缺一条就会写出骗人的界面：
 *
 * 1. **界面该有的位置一律放出来**——九类字段一项不少，让缺哪些后端能力一眼可见；
 * 2. **UI 决定「要有什么功能」，不决定「数据可不可信」**——后端没有的东西一律如实说，绝不摆一个编出来
 *    的值。所以这一页**不新增任何 API**：AI 生成草稿、附件上传、下发都还没有接进来，界面上逐处写明；
 *    「存草稿」落本机 `localStorage`，也写明它只在这台电脑上。
 *
 * 「发布」是全站唯一允许用「蓝字 + 蓝色高光内边」主动作样式的按钮（2026-09-02 裁决的例外只归本页），
 * 玻璃本体仍不填色。它是**可点**的：点之前页脚已经写清下发还没接进来，点之后给的是一句如实结论，
 * 不是一个假的成功。
 */

const SAVE_NOTICE =
  '草稿存在这台电脑上，换台电脑就没有了。';
const SAVE_FAILED_NOTICE =
  '浏览器不让存东西，草稿没存上。先把要点抄到别处。';
const PUBLISH_BLOCKED_NOTICE =
  '下发还没有接进来，现在发不出去。下一步：先存草稿。';
const TITLE_REQUIRED_NOTICE = '还没有填标题。先把标题填上。';

export default function WorkDispatchPage() {
  const [draft, setDraft] = useState<DispatchDraft>(() => loadDraft());
  const [targetInput, setTargetInput] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  const update = <Key extends keyof DispatchDraft>(
    key: Key,
    value: DispatchDraft[Key],
  ) => {
    setNotice(null);
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const addTarget = () => {
    const added = targetInput.trim();
    if (added.length === 0) {
      return;
    }
    setTargetInput('');
    update('targets', dedupeTargets([...draft.targets, added]));
  };

  const removeTarget = (target: string) => {
    update(
      'targets',
      draft.targets.filter((item) => item !== target),
    );
  };

  const toggleReminder = (choice: ReminderChoice) => {
    const selected = draft.reminders.includes(choice)
      ? draft.reminders.filter((item) => item !== choice)
      : REMINDER_CHOICES.filter(
          (item) => item === choice || draft.reminders.includes(item),
        );
    update('reminders', selected);
  };

  const publish = () => {
    setNotice(
      draft.title.trim().length === 0
        ? TITLE_REQUIRED_NOTICE
        : PUBLISH_BLOCKED_NOTICE,
    );
  };

  const store = () => {
    setNotice(saveDraft(draft) ? SAVE_NOTICE : SAVE_FAILED_NOTICE);
  };

  const targetCount = draft.targets.length;

  return (
    <div className={styles.page}>
      <h1 className={styles.pageTitle}>任务交办</h1>

      <section className={styles.brief}>
        <label className={styles.srOnly} htmlFor="dispatch-brief">
          用一句话说明要交办的事
        </label>
        <Input.TextArea
          autoSize={{ maxRows: 3, minRows: 1 }}
          className={styles.briefInput}
          id="dispatch-brief"
          onChange={(event) => update('brief', event.target.value)}
          placeholder="例：让各科室 9 月 5 日前报送第三季度政务信息"
          value={draft.brief}
          variant="borderless"
        />
        <div className={styles.briefBar}>
          <span className={styles.caption}>
            用一句话说明交办事项即可；也可跳过，直接在下方逐项填写。
          </span>
          <Button className={styles.briefButton} disabled type="primary">
            <Icon name="spark" size={17} strokeWidth={1.9} />
            生成草稿
          </Button>
        </div>
        <p className={styles.caption}>
          自动把这句话拆成下面的字段还没有接进来，请自己逐项填。
        </p>
      </section>

      <section className={styles.panel}>
        <div className={styles.draftNotice}>
          <span className={styles.draftMark}>
            <Icon name="alert" size={17} strokeWidth={1.9} />
          </span>
          <b className={styles.draftTitle}>这是 AI 生成的草稿，尚未发布</b>
          <span className={styles.draftHint}>
            逐项核对无误后，点右下角「发布」才会下发
          </span>
        </div>

        <div className={styles.form}>
          <h2 className={styles.sectionHead}>
            <span>基本信息</span>
            <i aria-hidden="true" />
          </h2>
          <div className={styles.gridKindTitle}>
            <div className={styles.field}>
              <label htmlFor="dispatch-kind">类型</label>
              <Select<DispatchKind>
                className={styles.select}
                id="dispatch-kind"
                onChange={(value) => update('kind', value)}
                options={DISPATCH_KINDS.map((kind) => ({
                  label: kind,
                  value: kind,
                }))}
                value={draft.kind}
                virtual={false}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="dispatch-title">标题</label>
              <Input
                id="dispatch-title"
                onChange={(event) => update('title', event.target.value)}
                value={draft.title}
              />
            </div>
          </div>

          <h2 className={styles.sectionHead}>
            <span>交办范围与时限</span>
            <i aria-hidden="true" />
          </h2>
          <div className={styles.gridScope}>
            <div className={styles.field}>
              <label htmlFor="dispatch-assignee">责任人 / 责任部门</label>
              <Input
                id="dispatch-assignee"
                onChange={(event) => update('assignee', event.target.value)}
                value={draft.assignee}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="dispatch-due">截止时间</label>
              {/*
                日期时间选择器用浏览器自带的那一个：麒麟上的 Chromium 会弹出系统日历，用户不用学新控件，
                也不用为它多引一个日期库。它不是文本框——填的是年月日和时分，不接受随手打的一句话。
              */}
              <input
                className={styles.dateInput}
                id="dispatch-due"
                onChange={(event) => update('dueAt', event.target.value)}
                type="datetime-local"
                value={draft.dueAt}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="dispatch-visibility">可见范围</label>
              <Input
                id="dispatch-visibility"
                onChange={(event) => update('visibility', event.target.value)}
                value={draft.visibility}
              />
            </div>
          </div>

          <div className={styles.field}>
            <label htmlFor="dispatch-target">交办对象</label>
            <div className={styles.chipWell}>
              {draft.targets.map((target) => (
                <span className={styles.chip} key={target}>
                  {target}
                  <button
                    aria-label={`删除交办对象 ${target}`}
                    className={styles.chipRemove}
                    onClick={() => removeTarget(target)}
                    type="button"
                  >
                    <Icon name="close" size={14} strokeWidth={2.3} />
                  </button>
                </span>
              ))}
              <Input
                className={styles.chipInput}
                id="dispatch-target"
                onChange={(event) => setTargetInput(event.target.value)}
                onPressEnter={addTarget}
                placeholder="输入一个科室或姓名"
                value={targetInput}
                variant="borderless"
              />
              <Button className={styles.chipAdd} onClick={addTarget}>
                <Icon name="plus" size={15} strokeWidth={2.2} />
                添加
              </Button>
            </div>
            <p className={styles.caption}>
              {targetCount === 0
                ? '还没有交办对象，在这里一个一个添加。'
                : `已添加交办对象 ${targetCount} 个，重复添加的会自动去掉。`}
            </p>
          </div>

          <h2 className={styles.sectionHead}>
            <span>办理要求与回执</span>
            <i aria-hidden="true" />
          </h2>
          <div className={styles.gridRequirement}>
            <div className={styles.field}>
              <label htmlFor="dispatch-requirement">办理要求与交付物</label>
              <Input.TextArea
                id="dispatch-requirement"
                onChange={(event) => update('requirement', event.target.value)}
                rows={2}
                value={draft.requirement}
              />
            </div>
            <div className={styles.field}>
              <span className={styles.fieldLabel} id="dispatch-attachment-label">
                附件
              </span>
              <div
                aria-labelledby="dispatch-attachment-label"
                className={styles.attachmentWell}
                role="group"
              >
                <Button className={styles.chipAdd} disabled>
                  <Icon name="plus" size={15} strokeWidth={2.2} />
                  添加附件
                </Button>
              </div>
              <p className={styles.caption}>
                Word / PDF / 图片，单个不超过 20 MB。附件还传不上去。
              </p>
            </div>
            <div className={styles.field}>
              <label htmlFor="dispatch-receipt">回执要求</label>
              <Input
                id="dispatch-receipt"
                onChange={(event) => update('receipt', event.target.value)}
                value={draft.receipt}
              />
            </div>
            <div className={styles.field}>
              <span className={styles.fieldLabel} id="dispatch-reminder-label">
                提醒策略（可多选，各提醒一次）
              </span>
              <div
                aria-labelledby="dispatch-reminder-label"
                className={styles.reminderRow}
                role="group"
              >
                {REMINDER_CHOICES.map((choice) => {
                  const selected = draft.reminders.includes(choice);
                  return (
                    <button
                      aria-pressed={selected}
                      className={selected ? styles.reminderOn : styles.reminderOff}
                      key={choice}
                      onClick={() => toggleReminder(choice)}
                      type="button"
                    >
                      {choice}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        <footer className={styles.footer}>
          <div className={styles.footerCopy}>
            <p className={styles.caption}>
              {targetCount === 0
                ? '发布前对方不可见。'
                : `发布后，${targetCount} 个交办对象的工作事项中各生成一条；发布前对方不可见。`}
            </p>
            <p className={styles.footerWarn}>
              下发还没有接进来，点「发布」发不出去；「存草稿」只存这台电脑。
            </p>
          </div>
          <div className={styles.footerActions}>
            <Button className={styles.footerButton} onClick={store}>
              存草稿
            </Button>
            <Button
              className={`${styles.footerButton} ${styles.publishButton}`}
              onClick={publish}
            >
              {/* 用 span 包住：antd 会给两个汉字的按钮文字中间自动插一个空格，「发 布」不是我们要的字样。 */}
              <span>发布</span>
            </Button>
          </div>
        </footer>
        {notice === null ? null : (
          <p className={styles.notice} role="status">
            {notice}
          </p>
        )}
      </section>
    </div>
  );
}
