import { ImWorkspace } from './ImWorkspace';

/**
 * 已不再使用：lane 3 起合成数据改在 `useImLifecycle` 选择 transport 时接入，MessagesPage 直接挂 ImWorkspace。
 * 删除本文件需雨爷专项授权，授权前保持可编译。
 */
export default function DevImWorkspace() {
  return <ImWorkspace />;
}
