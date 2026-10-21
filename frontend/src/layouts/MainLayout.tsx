import { useMemo, useState } from 'react'
import { Avatar, Button, Dropdown, Layout, Menu, Tag, Tooltip, Typography } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  ApiOutlined,
  AuditOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  FileTextOutlined,
  HeartOutlined,
  KeyOutlined,
  LineChartOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PartitionOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  ShopOutlined,
  SyncOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons'

const { Sider, Content, Header } = Layout

/** 一级菜单：看板（P0 业务入口）在最上面，数据工具归到一组 */
const PRIMARY_ITEMS = [
  { key: '/dashboards/ops', icon: <LineChartOutlined />, label: '运营看板' },
  { key: '/dashboards/sales', icon: <ShopOutlined />, label: '销售看板' },
  {
    type: 'group' as const,
    label: '数据工具',
    children: [
      { key: '/', icon: <DatabaseOutlined />, label: '数据集' },
      { key: '/flows', icon: <DeploymentUnitOutlined />, label: '数据编排' },
      { key: '/data-assistant', icon: <RobotOutlined />, label: '数据助手' },
      { key: '/my-keys', icon: <KeyOutlined />, label: '我的 API Key' },
      { key: '/my-reports', icon: <FileTextOutlined />, label: '我的报表' },
    ],
  },
]

export default function MainLayout() {
  const nav = useNavigate()
  const loc = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const user = useMemo(() => JSON.parse(localStorage.getItem('user') || 'null'), [])

  const items = useMemo(() => {
    if (!user?.is_admin) return PRIMARY_ITEMS
    return [
      ...PRIMARY_ITEMS,
      {
        type: 'group' as const,
        label: '管理',
        children: [
          { key: '/admin/users', icon: <TeamOutlined />, label: '用户管理' },
          { key: '/admin/datasets', icon: <SettingOutlined />, label: '数据集与授权' },
          {
            key: '/admin/grant-requests',
            icon: <SafetyCertificateOutlined />,
            label: '权限申请审批',
          },
          { key: '/admin/quality', icon: <HeartOutlined />, label: '数据质量监控' },
          { key: '/admin/metric-lineage', icon: <PartitionOutlined />, label: '指标血缘' },
          { key: '/admin/providers', icon: <ApiOutlined />, label: '模型配置' },
          { key: '/admin/sync', icon: <SyncOutlined />, label: '同步任务' },
          { key: '/admin/audit', icon: <AuditOutlined />, label: '审计日志' },
          ...(user?.is_superadmin
            ? [{ key: '/admin/tenants', icon: <ClusterOutlined />, label: '租户管理' }]
            : []),
        ],
      },
    ]
  }, [user])

  // 子页面要把菜单高亮回它所属的一级项
  const selectedKey = loc.pathname.startsWith('/flows')
    ? '/flows'
    : loc.pathname.startsWith('/datasets')
      ? '/'
      : loc.pathname.startsWith('/dashboards')
        ? loc.pathname   // 看板各自独立高亮
        : loc.pathname

  const initial = (user?.display_name || user?.username || '?').trim().charAt(0).toUpperCase()

  const logout = () => {
    localStorage.clear()
    nav('/login')
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        className="bisql-sider"
        theme="dark"
        width={200}
        collapsedWidth={56}
        collapsed={collapsed}
        style={{ position: 'sticky', top: 0, height: '100vh', overflow: 'auto' }}
      >
        <div className="bisql-brand">
          <div className="bisql-brand__mark">BI</div>
          {!collapsed && (
            <div className="bisql-brand__text">
              <span className="bisql-brand__title">BIsql 中台</span>
              <span className="bisql-brand__sub">统一权限分发</span>
            </div>
          )}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={items}
          onClick={(e) => nav(e.key)}
          style={{ borderInlineEnd: 'none', paddingTop: 6 }}
        />
      </Sider>

      <Layout>
        <Header className="bisql-header" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Tooltip title={collapsed ? '展开菜单' : '收起菜单'}>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((v) => !v)}
            />
          </Tooltip>

          <div style={{ flex: 1 }} />

          {user?.is_admin && (
            <Tooltip title="管理员：可审批权限申请、查看审计与质量监控">
              <Tag color="blue" style={{ margin: 0 }}>
                管理员
              </Tag>
            </Tooltip>
          )}
          {user?.is_superadmin && <Tag color="purple">平台超管</Tag>}

          <Dropdown
            placement="bottomRight"
            menu={{
              items: [
                {
                  key: 'who',
                  disabled: true,
                  label: (
                    <div style={{ padding: '2px 0' }}>
                      <div style={{ fontWeight: 600 }}>{user?.display_name || user?.username}</div>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {user?.is_admin ? '管理员' : '普通用户'}
                        {user?.is_superadmin ? ' · 平台超管' : ''}
                      </Typography.Text>
                    </div>
                  ),
                },
                { type: 'divider' },
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  onClick: logout,
                },
              ],
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                cursor: 'pointer',
                padding: '3px 7px',
                borderRadius: 'var(--radius-sm)',
              }}
            >
              <Avatar
                size={26}
                className="bisql-avatar"
                icon={<UserOutlined style={{ color: 'var(--brand-primary)' }} />}
              >
                {initial}
              </Avatar>
              <Typography.Text style={{ fontSize: 12.5, fontWeight: 500 }}>
                {user?.display_name || user?.username}
              </Typography.Text>
            </div>
          </Dropdown>
        </Header>

        <Content>
          <div className="bisql-page">
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
