import { ConfigProvider, App as AntApp, Flex, Layout, Space } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import RoleSelector from './components/RoleSelector';
import HealthPage from './pages/HealthPage';
import BindingsPage from './pages/admin/BindingsPage';
import RegistryPage from './pages/admin/RegistryPage';
import TasksPage from './pages/admin/TasksPage';

const { Header, Content } = Layout;
const queryClient = new QueryClient();

export default function App() {
  return (
    <ConfigProvider>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <Layout style={{ minHeight: '100vh' }}>
              <Header style={{ height: 'auto', paddingBlock: 12 }}>
                <Flex align="center" justify="space-between" gap={24} wrap>
                  <Space size="large">
                    <Link to="/health" style={{ color: '#fff' }}>
                      EternalAI
                    </Link>
                    <Link to="/admin/registry" style={{ color: '#fff' }}>
                      Registry 管理
                    </Link>
                    <Link to="/admin/tasks" style={{ color: '#fff' }}>
                      Task 证据
                    </Link>
                    <Link to="/admin/bindings" style={{ color: '#fff' }}>
                      Binding 查看
                    </Link>
                  </Space>
                  <RoleSelector />
                </Flex>
              </Header>
              <Content style={{ padding: 24 }}>
                <Routes>
                  <Route path="/" element={<HealthPage />} />
                  <Route path="/health" element={<HealthPage />} />
                  <Route path="/admin/registry" element={<RegistryPage />} />
                  <Route path="/admin/tasks" element={<TasksPage />} />
                  <Route path="/admin/bindings" element={<BindingsPage />} />
                </Routes>
              </Content>
            </Layout>
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
