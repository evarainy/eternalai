import { PlaceholderPage } from '../../shared/ui/PlaceholderPage';

export default function WorkDispatchLandingPage() {
  return (
    <PlaceholderPage
      title="任务交办"
      icon="send"
      reason="任务交办还没有开发，这里派不了活，也存不了草稿。"
      nextStep="现在要派活，请照原来的方式在 OA 里发；已经派出去的事在「工作事项」里跟进度。"
      actions={[{ label: '去工作事项', to: '/work-objects' }]}
    />
  );
}
