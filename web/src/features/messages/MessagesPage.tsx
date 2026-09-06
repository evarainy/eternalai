import { PlaceholderPage } from '../../shared/ui/PlaceholderPage';

export default function MessagesPage() {
  return (
    <PlaceholderPage
      title="消息"
      icon="mail"
      reason="消息功能还没有开发，这里收不到也发不出消息。"
      nextStep="OA 里的通知和待办，请照原来的方式直接去 OA 查看。"
      actions={[{ label: '去工作事项', to: '/work-objects' }]}
    />
  );
}
