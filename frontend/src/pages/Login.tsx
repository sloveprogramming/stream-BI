import { Button, Card, Form, Input, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import http from '../api/http'

export default function Login() {
  const nav = useNavigate()
  const onFinish = async (v: { username: string; password: string }) => {
    try {
      const { data } = await http.post('/auth/login', v)
      localStorage.setItem('token', data.token)
      localStorage.setItem('user', JSON.stringify(data.user))
      nav('/')
    } catch {
      message.error('用户名或密码错误')
    }
  }
  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#f0f2f5' }}>
      <Card title="BIsql 数据分发中台" style={{ width: 380 }}>
        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input placeholder="用户名" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password placeholder="密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
