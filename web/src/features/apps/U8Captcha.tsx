import { useEffect, useState } from 'react';
import { Modal } from 'antd';
import { getU8Captcha, readU8Captcha } from '../../api/systemEntry';
import type { U8CaptchaAnswer, U8CaptchaView } from '../../api/systemEntry';
import styles from './Systems.module.css';

/** The image and GUID are transient; the answer stays in the current form DOM. */
export function U8CaptchaFields({ disabled }: { disabled: boolean }) {
  const [revision, setRevision] = useState(0);
  const [challenge, setChallenge] = useState<U8CaptchaView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setChallenge(null);
    setError(null);
    void getU8Captcha(controller.signal).then((value) => {
      if (!controller.signal.aborted) setChallenge(value);
    }).catch(() => {
      if (!controller.signal.aborted) setError('验证码加载失败，请重试。');
    });
    return () => controller.abort();
  }, [revision]);

  return (
    <>
      <label className={styles.bindingField} data-focus-ring="host">
        <span>验证码</span>
        <input autoComplete="off" disabled={disabled || challenge === null} key={challenge?.guid ?? ''}
          name="u8_code" type="text" />
      </label>
      <input name="u8_guid" type="hidden" value={challenge?.guid ?? ''} />
      <div className={styles.captchaRow}>
        {challenge !== null ? <img alt="财务登录验证码" src={challenge.image} />
          : <span role={error === null ? 'status' : 'alert'}>{error ?? '正在加载验证码…'}</span>}
        <button className="wb-btn wb-btn-sm" disabled={disabled} onClick={() => setRevision((value) => value + 1)} type="button">
          {error === null ? '换一张' : '重试'}
        </button>
      </div>
    </>
  );
}

export function U8EntryCaptcha({ busy, error, revision, onCancel, onSubmit }: {
  busy: boolean;
  error: string | null;
  revision: number;
  onCancel: () => void;
  onSubmit: (answer: U8CaptchaAnswer) => void;
}) {
  const [missing, setMissing] = useState(false);
  return (
    <Modal footer={null} onCancel={() => { if (!busy) onCancel(); }} open title="进入财务系统" width={440}>
      <form className={styles.binding} onSubmit={(event) => {
        event.preventDefault();
        const answer = readU8Captcha(new FormData(event.currentTarget));
        setMissing(answer === null);
        if (answer !== null) onSubmit(answer);
      }}>
        <p>请填写下图验证码，继续使用已绑定的账号登录。</p>
        <U8CaptchaFields disabled={busy} key={revision} />
        {missing ? <p className={styles.bindingNote} data-tone="error" role="alert">请先加载并填写验证码。</p> : null}
        {error !== null ? <p className={styles.bindingNote} data-tone="error" role="alert">{error}</p> : null}
        <div className={styles.bindingActions}>
          <button className="wb-btn wb-btn-primary wb-btn-sm" disabled={busy} type="submit">
            {busy ? '正在登录…' : '登录'}
          </button>
          <button className="wb-btn wb-btn-sm" disabled={busy} onClick={onCancel} type="button">取消</button>
        </div>
      </form>
    </Modal>
  );
}
