import { Layout, Menu, Button, Space, Typography } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  DatabaseOutlined, KeyOutlined, FileTextOutlined, TeamOutlined,
  SettingOutlined, SyncOutlined, AuditOutlined, LogoutOutlined,
} from '@ant-design/icons'

const { Sider, Content, Header } = Layout

export default function MainLayout() {
  const nav = useNavigate()
  const loc = useLocation()
  const user = JSON.parse(localStorage.getItem('user') || 'null')

  const items = [
    { key: '/', icon: <DatabaseOutlined />, label: '数据集' },
    { key: '/my-keys', icon: <KeyOutlined />, label: '我的 API Key' },
    { key: '/my-reports', icon: <FileTextOutlined />, label: '我的报表' },
    ...(user?.is_admin
      ? [
          { type: 'group' as const, label: '管理员', children: [
            { key: '/admin/users', icon: <TeamOutlined />, label: '用户管理' },
            { key: '/admin/datasets', icon: <SettingOutlined />, label: '数据集与授权' },
            { key: '/admin/sync', icon: <SyncOutlined />, label: '同步任务' },
            { key: '/admin/audit', icon: <AuditOutlined />, label: '审计日志' },
          ]},
        ]
      : []),
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="dark" width={220}>
        <div style={{ color: '#fff', padding: 20, fontWeight: 700, fontSize: 16 }}>BIsql 中台</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[loc.pathname]}
          items={items}
          onClick={(e) => nav(e.key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', paddingInline: 24 }}>
          <Space>
            <Typography.Text>{user?.display_name || user?.username}</Typography.Text>
            <Button
              icon={<LogoutOutlined />}
              onClick={() => {
                localStorage.clear()
                nav('/login')
              }}
            >
              退出
            </Button>
          </Space>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
