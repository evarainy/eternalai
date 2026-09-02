import { Link } from 'react-router-dom';
import { PlaceholderPage } from '../../shared/ui/PlaceholderPage';

export default function WorkDispatchLandingPage() {
  return (
    <PlaceholderPage
      title="任务交办"
      unavailable="任务交办还没有开发。这个页面现在派不了活，也存不了草稿，填了也发不出去。"
      planned={[
        '先用一句话把要派的活说清楚，例如「让张三周五前把上月台账交上来，要回执」。',
        '说完之后就在同一页展开成一份草稿，逐项摊开给你看：类型、标题、责任人或责任部门、截止时间、办理要求与交付物、回执要求、提醒策略、可见范围、交办对象。',
        '草稿只是草稿。看过、改过之后，要你自己点「发布」才真的派出去；发出去后还留一小段时间可以撤回。',
      ]}
      alternatives={[
        '现在要派活，请照原来的方式在 OA 里发。',
        <>
          已经派出去的事，可以在
          <Link to="/work-objects">工作事项</Link>
          里跟进度。
        </>,
        '拿不准怎么写要求，可以先问 AI 助手，让它帮你把话理清楚，再自己去 OA 发。',
      ]}
    />
  );
}
