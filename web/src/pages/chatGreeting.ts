/**
 * AI 助手页空态标题用的问候语。
 *
 * 画板 `Chat.dc.html` 画的是「王主任，早上好」。姓名与职务至今没有后端读取端点（顶栏同一处仍是
 * fail-closed 占位），编一个名字就是造数据，所以只落**不带称呼**的问候。时段取本机时钟——那是真实
 * 值，不是编出来的。
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
