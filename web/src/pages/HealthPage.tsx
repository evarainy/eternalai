import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Descriptions, Space, Spin, Tag, Typography } from 'antd';
import SelfCheckPanel from '../features/background-jobs/SelfCheckPanel';
import { CHECK_TEXT, readHealth } from '../features/background-jobs/selfCheckApi';

const { Title } = Typography;

export default function HealthPage() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: readHealth,
    refetchInterval: 15000,
  });
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Title level={3}>系统状态</Title>
      <Card title="服务状态">
        {health.isPending && <Spin />}
        {health.isError && <Alert type="error" showIcon message="暂时无法获取系统状态，请稍后刷新。" />}
        {health.data && (
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="总体">
              <Tag color={health.data.status === 'ok' ? 'green' : 'red'}>
                {health.data.status === 'ok' ? '正常' : '异常'}
              </Tag>
            </Descriptions.Item>
            {Object.entries(health.data.checks).map(([name, value]) => (
              <Descriptions.Item key={name} label={CHECK_TEXT[name] ?? name}>
                <Tag color={value === 'ok' ? 'green' : 'red'}>{value === 'ok' ? '正常' : '异常'}</Tag>
              </Descriptions.Item>
            ))}
          </Descriptions>
        )}
      </Card>
      <SelfCheckPanel />
    </Space>
  );
}
