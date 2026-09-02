import { Link } from 'react-router-dom';
import { PlaceholderPage } from '../../shared/ui/PlaceholderPage';

export default function AppsPage() {
  return (
    <PlaceholderPage
      title="软件中心"
      unavailable="软件中心还没有开发。这个页面现在看不到任何软件，也打不开任何软件。"
      planned={[
        '业务系统：OA、文件系统和其他日常要用的系统入口，并写明你当前有没有登录。',
        '单位软件：单位审核发布、你本人有权使用的软件。',
        '我的功能：你本人已经有的功能，会写明负责人、风险高低和是否要先绑定账号。',
        '以后还能自己登记一个软件：填名称、一句话说明、访问地址、归属科室和负责人；提交审核后才对别人可见。',
      ]}
      alternatives={[
        'OA 请照原来的方式打开，工作台现在还不代管这些入口。',
        <>
          和账号有关的绑定，请去
          <Link to="/admin/bindings">账号绑定</Link>。
        </>,
        '想用工作台已经能办的事，请直接问 AI 助手，它会告诉你办不办得了。',
      ]}
    />
  );
}
