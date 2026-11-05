import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Select, Space, Tabs, Tag } from 'antd';
import { useAuthStore } from '../../stores/authStore';
import { DELIVERY_STATE_LABELS, deliveryTime } from './deliveryApi';
import {
  grantAgentMaterial, personalAgentError, publishAgentMaterial, readAgentGrants,
  readAgentMaterials, readAgentRecipients, readAgentRequests, readPersonalAgents,
  REQUEST_STATE_LABELS, revokeAgentGrant, withdrawAgentMaterial,
} from './personalAgentApi';
import type {
  AgentGrant, AgentMaterial, AgentRecipient, AgentRequest, GrantMaterial,
  PersonalAgent, PublishMaterial,
} from './personalAgentApi';
import styles from './personalAgent.module.css';

function useCurrentIdentity() {
  const generation = useAuthStore((state) => state.generation);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);
  return useCallback(() => live.current && useAuthStore.getState().generation === generation
    && useAuthStore.getState().status === 'authenticated', [generation]);
}

function AgentMaterials({ agent }: { agent: PersonalAgent }) {
  const current = useCurrentIdentity();
  const [materials, setMaterials] = useState<AgentMaterial[]>([]);
  const [grants, setGrants] = useState<AgentGrant[]>([]);
  const [recipients, setRecipients] = useState<AgentRecipient[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [materialDeadline, setMaterialDeadline] = useState('');
  const [version, setVersion] = useState<number>();
  const [recipient, setRecipient] = useState<string>();
  const [grantDeadline, setGrantDeadline] = useState('');
  const [publication, setPublication] = useState<PublishMaterial | null>(null);
  const [authorization, setAuthorization] = useState<GrantMaterial | null>(null);

  const refresh = useCallback(async () => {
    const [nextMaterials, nextGrants, nextRecipients] = await Promise.all([
      readAgentMaterials(agent.agent_id), readAgentGrants(agent.agent_id), readAgentRecipients(agent.agent_id),
    ]);
    if (current()) {
      setMaterials(nextMaterials.materials);
      setGrants(nextGrants.grants);
      setRecipients(nextRecipients.recipients);
    }
  }, [agent.agent_id, current]);

  const run = useCallback(async (action: () => Promise<unknown>) => {
    if (inFlight.current || !current()) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try { await action(); }
    catch (failure) { if (current()) setError(personalAgentError(failure)); }
    finally {
      inFlight.current = false;
      if (current()) setBusy(false);
    }
  }, [current]);

  useEffect(() => { void run(refresh); }, [refresh, run]);

  const materialExpiry = Date.parse(materialDeadline);
  const grantExpiry = Date.parse(grantDeadline);
  const selectedMaterial = materials.find((material) => material.version === version);
  const publishValid = title.trim().length > 0 && body.trim().length > 0
    && Number.isFinite(materialExpiry) && materialExpiry > Date.now();
  const grantValid = recipient !== undefined && selectedMaterial !== undefined
    && selectedMaterial.withdrawn_at === null && Number.isFinite(grantExpiry)
    && grantExpiry > Date.now() && grantExpiry <= Date.parse(selectedMaterial.expires_at);
  const publish = () => {
    if (publication === null) return;
    const fixed = publication;
    setPublication(null);
    void run(async () => {
      await publishAgentMaterial(agent.agent_id, fixed);
      if (!current()) return;
      setTitle(''); setBody(''); setMaterialDeadline('');
      await refresh();
    });
  };
  const grant = () => {
    if (authorization === null) return;
    const fixed = authorization;
    setAuthorization(null);
    void run(async () => {
      await grantAgentMaterial(agent.agent_id, fixed);
      if (current()) await refresh();
    });
  };

  return <div>
    <Space wrap>
      <strong>{agent.display_name}</strong><Tag>{agent.status === 'active' ? '已开通' : '已停用'}</Tag>
      <Button loading={busy} onClick={() => { void run(refresh); }}>刷新资料与授权</Button>
    </Space>
    <p>助手仅依据你明确发布并按人授权的资料代答。你退出登录后授权继续有效，已发出的消息无法召回。</p>
    {error ? <Alert type="warning" showIcon title={error} /> : null}
    <div className={styles.columns}>
      <section className={styles.form} aria-label="发布助手资料">
        <strong>发布新版本</strong>
        <label htmlFor="agent-material-title">资料标题</label>
        <Input id="agent-material-title" value={title} disabled={busy || agent.status !== 'active'}
          onChange={(event) => setTitle(event.target.value)} />
        <label htmlFor="agent-material-body">我有权分享的文本</label>
        <Input.TextArea id="agent-material-body" value={body} disabled={busy || agent.status !== 'active'}
          autoSize={{ minRows: 5, maxRows: 14 }} onChange={(event) => setBody(event.target.value)} />
        <label htmlFor="agent-material-deadline">资料到期时间（本机时间）</label>
        <Input id="agent-material-deadline" type="datetime-local" value={materialDeadline}
          disabled={busy || agent.status !== 'active'} onChange={(event) => setMaterialDeadline(event.target.value)} />
        <p>发布后正文不可修改；更新内容请另发新版本，已有授权不会自动改为新版本。</p>
        <Button disabled={busy || agent.status !== 'active' || !publishValid} onClick={() => {
          setPublication({ title, body, expires_at: new Date(materialExpiry).toISOString() });
        }}>查看待发布内容</Button>
      </section>
      <section className={styles.form} aria-label="授予资料代答权限">
        <strong>按人授权</strong>
        <label htmlFor="agent-grant-material">资料版本</label>
        <Select id="agent-grant-material" value={version} disabled={busy || agent.status !== 'active'}
          placeholder="选择已发布且有效的版本" onChange={setVersion}
          options={materials.filter((material) => material.withdrawn_at === null
            && Date.parse(material.expires_at) > Date.now()).map((material) => ({
            value: material.version, label: `v${material.version} · ${material.title}`,
          }))} />
        <label htmlFor="agent-grant-recipient">仅向这位同事私回</label>
        <Select id="agent-grant-recipient" value={recipient} disabled={busy || agent.status !== 'active'}
          showSearch={{ optionFilterProp: 'label' }} placeholder="选择已核验 IM 身份的同事" onChange={setRecipient}
          options={recipients.map((person) => ({ value: person.ai_user_id,
            label: `${person.display_name}（${person.ai_user_id}）` }))} />
        <label htmlFor="agent-grant-deadline">授权截止时间（本机时间）</label>
        <Input id="agent-grant-deadline" type="datetime-local" value={grantDeadline}
          disabled={busy || agent.status !== 'active'} onChange={(event) => setGrantDeadline(event.target.value)} />
        {selectedMaterial ? <p>不得晚于资料到期时间：{deliveryTime(selectedMaterial.expires_at)}。</p> : null}
        <p>同一人的新授权会撤回旧授权；每次只回答这里选定的版本。</p>
        <Button disabled={busy || agent.status !== 'active' || !grantValid} onClick={() => {
          setAuthorization({ material_version: version!, grantee_ai_user_id: recipient!,
            expires_at: new Date(grantExpiry).toISOString() });
        }}>查看待授权内容</Button>
      </section>
    </div>
    <ul className={styles.records} aria-label="已发布资料版本">
      {materials.map((material) => <li key={material.version} className={styles.record}>
        <Space wrap><strong>v{material.version} · {material.title}</strong>
          <Tag>{material.withdrawn_at !== null ? '已撤回' : Date.parse(material.expires_at) <= Date.now() ? '已到期' : '有效'}</Tag>
        </Space>
        <p>发布于 {deliveryTime(material.published_at)} · 到期 {deliveryTime(material.expires_at)}</p>
        <details><summary className={styles.summary}>查看此版本正文</summary><pre className={styles.body}>{material.body}</pre></details>
        <p className={styles.hash}>SHA256 {material.content_sha256}</p>
        {material.withdrawn_at === null ? <Button danger disabled={busy} onClick={() => {
          void run(async () => { await withdrawAgentMaterial(agent.agent_id, material.version); if (current()) await refresh(); });
        }}>撤回此版本</Button> : null}
      </li>)}
    </ul>
    {!busy && materials.length === 0 && error === null ? <p>还未发布资料。先录入你有权分享的文本，再为同事授权。</p> : null}
    <ul className={styles.records} aria-label="资料授权记录">
      {grants.map((item) => {
        const material = materials.find((entry) => entry.version === item.material_version);
        const materialAvailable = material !== undefined && material.withdrawn_at === null
          && Date.parse(material.expires_at) > Date.now();
        return <li key={item.grant_id} className={styles.record}>
          <Space wrap><strong>{item.grantee_display_name}（{item.grantee_ai_user_id}）</strong>
            <Tag>{item.revoked_at !== null ? '已撤回' : Date.parse(item.expires_at) <= Date.now()
              ? '已到期' : !materialAvailable ? '资料已失效' : '有效'}</Tag>
          </Space>
          <p>资料 v{item.material_version} · 仅私回此人 · 截止 {deliveryTime(item.expires_at)}</p>
          {item.revoked_at === null ? <Button danger disabled={busy} onClick={() => {
            void run(async () => { await revokeAgentGrant(agent.agent_id, item.grant_id); if (current()) await refresh(); });
          }}>撤回对该同事的授权</Button> : null}
        </li>;
      })}
    </ul>
    <Modal title="发布此资料版本" open={publication !== null} onCancel={() => setPublication(null)}
      okText="发布此版本" cancelText="返回修改" onOk={publish} confirmLoading={busy}>
      {publication ? <div className={styles.form}>
        <strong>{publication.title}</strong><pre className={styles.body}>{publication.body}</pre>
        <p>到期：{deliveryTime(publication.expires_at)}</p><p>我确认有权分享以上文本，并允许获授权的同事通过我的助手查询。</p>
      </div> : null}
    </Modal>
    <Modal title="授予资料代答权限" open={authorization !== null} onCancel={() => setAuthorization(null)}
      okText="确认授权，仅私回该同事" cancelText="返回修改" onOk={grant} confirmLoading={busy}>
      {authorization ? <div className={styles.form}>
        <p>同事：{recipients.find((person) => person.ai_user_id === authorization.grantee_ai_user_id)?.display_name}
          （{authorization.grantee_ai_user_id}）</p>
        <p>资料：v{authorization.material_version} · {materials.find((material) => material.version === authorization.material_version)?.title}</p>
        <p>截止：{deliveryTime(authorization.expires_at)}</p><p>仅回答该版本资料，仅私回此人。同一人的旧授权将被撤回。</p>
      </div> : null}
    </Modal>
  </div>;
}

function AgentRequests() {
  const current = useCurrentIdentity();
  const [requests, setRequests] = useState<AgentRequest[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    if (!current()) return;
    setBusy(true); setError(null);
    try {
      const result = await readAgentRequests();
      if (current()) setRequests(result.requests);
    } catch (failure) { if (current()) setError(personalAgentError(failure)); }
    finally { if (current()) setBusy(false); }
  }, [current]);
  useEffect(() => { void refresh(); }, [refresh]);
  return <section aria-label="我的助手提问记录">
    <Button loading={busy} onClick={() => { void refresh(); }}>刷新提问状态</Button>
    <p>这里只列出你本人的提问状态。回复请在与相应助手的一对一私聊中查看。</p>
    {error ? <Alert type="warning" showIcon title={error} /> : null}
    {!busy && requests.length === 0 && error === null ? <p>还没有提问记录。请在单位消息中私聊同事的助手，或在群中明确点名该助手。</p> : null}
    <ul className={styles.records}>{requests.map((request) => <li key={request.inbox_id} className={styles.record}>
      <Space wrap><strong>{request.agent_display_name}</strong><Tag>{REQUEST_STATE_LABELS[request.state]}</Tag></Space>
      <p>提问于 {deliveryTime(request.received_at)} · 更新于 {deliveryTime(request.updated_at)}</p>
      {request.result_code === 'worker_interrupted' ? <Alert type="warning" showIcon
        title="处理已中断，请在 IM 中发一条新消息重新提问。原请求不会自动重跑。" /> : null}
      {request.state === 'completed' ? <p>AI 根据对方发布的授权资料代答，未经对方本人核实。处理完成不代表 IM 已接受回复。</p> : null}
      {request.deliveries.map((delivery) => <p key={delivery.delivery_id}>
        {delivery.reply_slot === 'private' ? '私聊回复' : '群内固定状态'}：{DELIVERY_STATE_LABELS[delivery.state]}
        {delivery.remote_message_id ? ` · 消息编号 ${delivery.remote_message_id}` : ''}
        <span className={styles.hash}> · 投递编号 {delivery.delivery_id}</span>
      </p>)}
      {request.deliveries.some((delivery) => delivery.state === 'unknown' || delivery.state === 'sending')
        ? <p>发送结果待核实，消息可能已经发送，不会自动重发。</p> : null}
    </li>)}</ul>
  </section>;
}

function OwnedAgents() {
  const current = useCurrentIdentity();
  const [agents, setAgents] = useState<PersonalAgent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const result = await readPersonalAgents();
      if (current()) setAgents(result.agents);
    } catch (failure) { if (current()) setError(personalAgentError(failure)); }
    finally { if (current()) setBusy(false); }
  }, [current]);
  useEffect(() => { void refresh(); }, [refresh]);
  const agent = agents[0];
  return <div>
    {error ? <Alert type="warning" showIcon title={error} action={<Button onClick={() => { void refresh(); }}>重新读取</Button>} /> : null}
    {busy ? <p role="status">正在读取我的助手。</p> : null}
    {!busy && agents.length === 0 && error === null ? <p>你的个人助手尚未开通，请联系维护人员核验并绑定具名助手账号。开通后可在这里发布资料和授权。</p> : null}
    {agent ? <AgentMaterials key={agent.agent_id} agent={agent} /> : null}
  </div>;
}

export function PersonalAgentPanel() {
  return <section className={styles.workspace} aria-label="个人助手资料与提问">
    <h2>个人助手</h2>
    <Tabs destroyOnHidden items={[
      { key: 'materials', label: '我的助手资料', children: <OwnedAgents /> },
      { key: 'requests', label: '我的提问', children: <AgentRequests /> },
    ]} />
  </section>;
}
