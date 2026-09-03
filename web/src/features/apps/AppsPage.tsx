import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from 'antd';
import { Link } from 'react-router-dom';
import { oaWorkbenchNavigation } from '../../contracts/runtimeProjection';
import { getBindingApiV1CredentialBindingsTargetSystemGet as getBinding } from '../../generated/credential-bindings/credential-bindings';
import type { CredentialBindingView } from '../../generated/credential-bindings/credential-bindings.schemas';
import { Icon } from '../../shared/ui/Icon';
import type { IconName } from '../../shared/ui/Icon';
import { useAuthStore } from '../../stores/authStore';
import { NewSoftwareDialog } from './NewSoftwareDialog';
import styles from './AppsPage.module.css';

/**
 * 软件中心，形态照定稿画板 `_scratch/design/glass/Apps.dc.html`。
 *
 * **只有一张卡是真的**：OA 办公系统。它的绑定状态取自 `credential-bindings` 的真实结果，「打开」走的是
 * 系统消息深链用的同一条 origin + 路径前缀校验（`oaWorkbenchNavigation`），新窗口打开；用户浏览器自带
 * OA 的会话 cookie，所以打开即已登录态，不需要新后端。
 *
 * 其余三个业务系统（财务、公文交换、督查督办）后端**没有任何数据源**：既没有它们的地址，也没有它们的
 * 绑定状态。按护栏「UI 决定要有什么功能，不决定数据可不可信」，这里**不摆**四张卡里三张写着编出来的
 * 「要先绑账号 / 未开通」，而是用一句话如实说明它们还没有接进来，并说清现在怎么办。「单位软件」「我的
 * 功能」两块同理：它们的数据在 `/admin/registry`（admin 上下文），2026-08-27 §三 明写软件中心与
 * `/admin/registry` 不合并、权限与字段不互借，因此本页不借用该接口，先如实占位。四处缺口都已按五字段
 * 登记为活欠债。
 */

/** 状态标签闭集（2026-09-02 雨爷第二轮意见）；读取中与读不到不属于绑定状态，另起中性两档，不冒充闭集里的任何一项。 */
type SystemStatus =
  | { kind: 'bound'; label: '已绑定'; icon: IconName }
  | { kind: 'needs_binding'; label: '要先绑账号'; icon: IconName }
  | { kind: 'loading'; label: '正在读取'; icon: IconName }
  | { kind: 'unknown'; label: '读不到'; icon: IconName };

function oaSystemStatus(
  binding: CredentialBindingView | undefined,
  fetch: 'loading' | 'error' | 'ready',
): SystemStatus {
  if (fetch === 'loading') {
    return { kind: 'loading', label: '正在读取', icon: 'clock' };
  }
  if (fetch === 'error' || binding === undefined) {
    return { kind: 'unknown', label: '读不到', icon: 'help' };
  }
  if (
    binding.bound &&
    binding.poll_status !== 'invalid' &&
    binding.poll_status !== 'captcha_required'
  ) {
    return { kind: 'bound', label: '已绑定', icon: 'check' };
  }
  return { kind: 'needs_binding', label: '要先绑账号', icon: 'alert' };
}

const STATUS_CLASS: Record<SystemStatus['kind'], string> = {
  bound: 'pillOk',
  needs_binding: 'pillWarn',
  loading: 'pillNeutral',
  unknown: 'pillNeutral',
};

/*
 * 三种拿不到链接的情形对用户是同一件事：这台服务器给不出可点的 OA 地址。分成三句话写，用户读到的
 * 区别只是措辞，能做的下一步完全一样。何况本入口的路径只来自已归一化的放行前缀表，`missing` 与
 * `untrusted` 两支实际走不到（见 `oaWorkbenchNavigation`），分三句等于为两个到不了的分支各写一段。
 */
const OA_LINK_UNAVAILABLE = 'OA 地址没配好，这里打不开。下一步：找管理员配一下。';

export default function AppsPage() {
  const authGeneration = useAuthStore((state) => state.generation);
  const [dialogOpen, setDialogOpen] = useState(false);

  const bindingQuery = useQuery({
    queryKey: ['credential-binding', authGeneration, 'oa'] as const,
    queryFn: () => getBinding('oa'),
  });
  const status = oaSystemStatus(
    bindingQuery.data,
    bindingQuery.isPending ? 'loading' : bindingQuery.isError ? 'error' : 'ready',
  );
  const navigation = oaWorkbenchNavigation();

  return (
    <div className={styles.page}>
      <section className={styles.panel}>
        <header className={styles.head}>
          <div className={styles.headCopy}>
            <h1 className={styles.title}>软件中心</h1>
            <p className={styles.aux}>
              单位在用的系统、单位发的软件、你自己的功能，都在这一页。
            </p>
          </div>
          <Button
            className={styles.newButton}
            onClick={() => setDialogOpen(true)}
            type="primary"
          >
            <Icon name="plus" size={19} strokeWidth={2.1} />
            新建应用
          </Button>
        </header>

        <div className={styles.sections}>
          <section className={styles.section}>
            <div className={styles.sectionHead}>
              <h2>业务系统</h2>
              <span className={styles.caption}>
                单位原来就在用的系统，都在新窗口打开，不会关掉你这一页
              </span>
            </div>
            <div className={styles.cardGrid}>
              <article className={styles.appCard}>
                <span className={styles.appMark}>
                  <Icon name="file" size={24} strokeWidth={1.9} />
                </span>
                <div className={styles.appBody}>
                  <div className={styles.appTitleLine}>
                    <span className={styles.appName}>OA 办公系统</span>
                    <span
                      className={styles[STATUS_CLASS[status.kind]]}
                      data-testid="oa-status"
                    >
                      <Icon name={status.icon} size={14} strokeWidth={2.4} />
                      {status.label}
                    </span>
                  </div>
                  {/*
                    画板这一行写的是「公文、审批、值班表 · 信息中心维护」。后半截是 `owner`——展示字段
                    白名单里的一项，但后端没有任何来源：谁维护 OA 是这个单位的组织事实，不是画板能替它
                    决定的，照抄就是编一个负责人。「值班表」同理，我们只确知 OA 里有公文与审批流。所以
                    这里只留说得出处的那半句，`owner` 一栏的缺口按五字段登记为欠债。
                  */}
                  <p className={styles.caption}>公文、审批等日常办公</p>
                </div>
                <div className={styles.appActions}>
                  {navigation.kind === 'allowed' ? (
                    <a
                      className={styles.openLink}
                      href={navigation.href}
                      rel="noopener noreferrer"
                      target="_blank"
                    >
                      打开
                      <Icon name="external" size={15} />
                    </a>
                  ) : null}
                  {status.kind === 'needs_binding' ? (
                    <Link className={styles.bindLink} to="/admin/bindings">
                      去绑账号
                    </Link>
                  ) : null}
                </div>
              </article>
              <p className={`${styles.sectionNote} ${styles.gridNote}`}>
                财务系统、公文交换平台、督查督办系统还没有接进来。下一步：这三个系统请照原来的方式打开。
              </p>
            </div>
            {navigation.kind === 'allowed' ? null : (
              <p className={styles.notice} role="status">
                {OA_LINK_UNAVAILABLE}
              </p>
            )}
            {status.kind === 'unknown' ? (
              <p className={styles.notice} role="status">
                读不到 OA 的绑定状态，这不等于没绑上。下一步：刷新本页；还是取不到就找管理员。
              </p>
            ) : null}
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHead}>
              <h2>单位软件</h2>
              <span className={styles.caption}>单位审核发布的，装上就能用</span>
            </div>
            <p className={styles.sectionNote}>
              单位发布的软件还没有接进来，这里一个也装不了。下一步：要装什么软件，先找信息中心。
            </p>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHead}>
              <h2>我的功能</h2>
              <span className={styles.caption}>能替你做一件小事的东西</span>
            </div>
            <p className={styles.sectionNote}>
              你自己的功能还没有接进来，这里看不到、也点不开。下一步：要查 OA 里的待办和消息，请到「AI 助手」里直接问。
            </p>
          </section>
        </div>
      </section>

      <NewSoftwareDialog onClose={() => setDialogOpen(false)} open={dialogOpen} />
    </div>
  );
}
