import { PlaceholderPage } from '../../shared/ui/PlaceholderPage';

export default function AppsPage() {
  return (
    <PlaceholderPage
      title="软件中心"
      icon="grid"
      reason="软件中心还没有开发，这里还看不到、也打不开任何软件。"
      nextStep="OA 请照原来的方式打开；要绑账号请去「账号绑定」。"
      actions={[{ label: '去账号绑定', to: '/admin/bindings' }]}
    />
  );
}
