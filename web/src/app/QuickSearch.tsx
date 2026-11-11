import Modal from 'antd/es/modal/Modal';
import { WorkObjectSearchField } from './WorkObjectSearchField';

/** Ctrl K 快速搜索工作事项（2026-11-11 去顶栏后由个人菜单与快捷键打开）。 */
export function QuickSearch({
  onClose,
  onSubmit,
  open,
}: {
  onClose: () => void;
  onSubmit: (value: string) => void;
  open: boolean;
}) {
  return (
    <Modal
      destroyOnHidden
      footer={null}
      onCancel={onClose}
      open={open}
      title="快速搜索"
      width={520}
    >
      <WorkObjectSearchField autoFocus initialValue="" onSubmit={onSubmit} />
    </Modal>
  );
}
