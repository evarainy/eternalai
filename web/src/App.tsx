import { ConfigProvider, App as AntApp, Layout } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import HealthPage from './pages/HealthPage';

const { Header, Content } = Layout;
const queryClient = new QueryClient();

export default function App() {
  return (
    <ConfigProvider>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <Layout style={{ minHeight: '100vh' }}>
              <Header>
                <Link to="/health" style={{ color: '#fff' }}>
                  EternalAI
                </Link>
              </Header>
              <Content style={{ padding: 24 }}>
                <Routes>
                  <Route path="/" element={<HealthPage />} />
                  <Route path="/health" element={<HealthPage />} />
                </Routes>
              </Content>
            </Layout>
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
