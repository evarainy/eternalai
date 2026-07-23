import { Flex, Select, Typography } from 'antd';
import { useRoleStore } from '../stores/roleStore';

const { Text } = Typography;

export default function RoleSelector() {
  const roles = useRoleStore((state) => state.roles);
  const setRoles = useRoleStore((state) => state.setRoles);

  return (
    <Flex vertical gap={4}>
      <Text strong style={{ color: '#fff' }}>
        角色声明（未认证声明 / unverified claim）
      </Text>
      <Select
        aria-label="角色声明"
        mode="tags"
        value={roles}
        onChange={setRoles}
        options={[{ label: 'admin', value: 'admin' }]}
        placeholder="选择或输入角色"
        tokenSeparators={[',']}
        style={{ minWidth: 280 }}
      />
      <Text style={{ color: '#d9d9d9', fontSize: 12 }}>
        仅用于 lite 管理面，后端不据此鉴真。
      </Text>
    </Flex>
  );
}
