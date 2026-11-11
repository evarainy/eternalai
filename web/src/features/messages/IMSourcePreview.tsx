import Button from 'antd/es/button';
import type { SourceResponse } from '../../generated/im-context/im-context.schemas';

export function IMSourcePreview({ source, onReturn, onClear }: {
  source: SourceResponse; onReturn: () => void; onClear: () => void;
}) {
  return <aside aria-label="消息来源">
    <p>已选择 {source.messages.length} 条消息，AI 会按你当前的权限分析。你也可以继续输入只读问题。</p>
    {source.messages.map((message) => <p key={message.message_id} style={{ whiteSpace: 'pre-wrap' }}>{message.text}</p>)}
    <Button onClick={onReturn}>返回原消息</Button>
    <Button onClick={onClear}>移除来源</Button>
  </aside>;
}
