import { useState } from 'react'
import { Alert, Button, Form, Input, Typography } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import http from '../api/http'

export default function Login() {
  const nav = useNavigate()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const onFinish = async (v: { username: string; password: string }) => {
    setError('')
    setLoading(true)
    try {
      const { data } = await http.post('/auth/login', v)
      localStorage.setItem('token', data.token)
      localStorage.setItem('user', JSON.stringify(data.user))
      nav('/')
    } catch (e: any) {
      setError(e?.response?.data?.detail || '登录失败，请检查用户名与密码')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
        background:
          'radial-gradient(1100px 550px at 18% 0%, #EFF6FF 0%, transparent 58%), ' +
          'radial-gradient(800px 450px at 100% 100%, #FFFBEB 0%, transparent 52%), ' +
          'var(--neutral-bg)',
      }}
    >
      <div
        style={{
          width: 380,
          background: 'var(--neutral-surface)',
          border: '1px solid var(--neutral-border)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-pop)',
          padding: '30px 28px 24px',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div
            className="bisql-brand__mark"
            style={{ width: 44, height: 44, fontSize: 16, margin: '0 auto 12px' }}
          >
            BI
          </div>
          <Typography.Title level={5} style={{ margin: 0, letterSpacing: 0 }}>
            BIsql 数据中台
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
            统一权限口径下的安全取数
          </Typography.Text>
        </div>

        {error && (
          <Alert
            type="error"
            showIcon
            message={error}
            style={{ marginBottom: 16 }}
            closable
            onClose={() => setError('')}
          />
        )}

        <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input
              prefix={<UserOutlined style={{ color: 'var(--neutral-text-tertiary)' }} />}
              placeholder="用户名"
            />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              prefix={<LockOutlined style={{ color: 'var(--neutral-text-tertiary)' }} />}
              placeholder="密码"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, marginTop: 20 }}>
            <Button type="primary" htmlType="submit" block loading={loading}>
              登录
            </Button>
          </Form.Item>
        </Form>
      </div>
    </div>
  )
}
