import { Link } from 'react-router-dom';
import { PlaceholderPage } from '../../shared/ui/PlaceholderPage';

export default function MessagesPage() {
  return (
    <PlaceholderPage
      title="消息"
      unavailable="消息功能还没有开发。这个页面现在收不到消息，也发不出消息，看不到任何未读提醒。"
      planned={[
        '把单位各个系统发来的通知汇到一处，不用再一个系统一个系统地翻。',
        'AI 从消息里看出像是要办的事时，只会先记成一条还没确认的事项；要你本人看过并确认，它才会变成正式的工作事项。',
        '消息只负责提醒你、把你带到该去的页面，不会替你直接办事，也不会跳过你的确认。',
        '工作台里的进展会以固定格式回贴到原来的消息里，不用你再回去手工说明。',
      ]}
      alternatives={[
        <>
          要办的事请看
          <Link to="/work-objects">工作事项</Link>。
        </>,
        'OA 里的通知和待办，请照原来的方式直接去 OA 查看。',
        '紧急的事仍然走电话或当面沟通，不要等这个页面。',
      ]}
    />
  );
}
