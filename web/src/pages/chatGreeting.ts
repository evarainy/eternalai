/**
 * AI 助手页空态标题用的问候语。
 *
 * 画板 `Chat.dc.html` 画的是「王主任，早上好」。称呼里的**姓名**现在有数据源了——
 * `GET /api/v1/me` 的 `display_name` 来自服务端签名的会话票据（`P2-USER-PROFILE-READ-001`），
 * 只要还登录着就一定有，所以问候语按画板带上称呼。画板里那个「主任」是**职务**，OA 的用户信息接口
 * 落盘字段里没有职务，编一个就是造数据，因此只用姓名。取不到姓名时退回不带称呼的问候，不留空位、
 * 不写占位名。时段取本机时钟——那是真实值。
 *
 * 单独成文件而不是挂在 `ChatPage.tsx` 上：那个文件只导出组件，混进一个函数会打破 Fast Refresh
 * （`react-refresh/only-export-components`）。
 */
export function greetingByHour(now: Date = new Date()): string {
  const hour = now.getHours();
  if (hour < 5) {
    return '夜里好';
  }
  if (hour < 11) {
    return '早上好';
  }
  if (hour < 13) {
    return '中午好';
  }
  if (hour < 18) {
    return '下午好';
  }
  return '晚上好';
}

export function greetingWithName(
  displayName: string | null,
  now: Date = new Date(),
): string {
  const greeting = greetingByHour(now);
  const name = displayName?.trim() ?? '';
  return name === '' ? greeting : `${name}，${greeting}`;
}
