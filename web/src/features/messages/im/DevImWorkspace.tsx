import { createDevTransport } from '../zulip/devTransport';
import { ImWorkspace } from './ImWorkspace';

/** 开发期入口：只在 `import.meta.env.DEV` 且 `VITE_IM_DEV_DATA=1` 时由 MessagesPage 动态加载。 */
export default function DevImWorkspace() {
  return <ImWorkspace createTransport={createDevTransport} />;
}
