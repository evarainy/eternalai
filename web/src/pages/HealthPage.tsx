import { Typography } from 'antd';
import { mockHealth } from '../api/mockHealth';

const { Title, Text } = Typography;

export default function HealthPage() {
  const health = mockHealth();
  return (
    <div>
      <Title level={3}>Health</Title>
      <Text>Status: {health.status}</Text>
    </div>
  );
}
