import { useEffect, useState } from 'react'
import { Card, Col, Row, Typography } from 'antd'
import { Link } from 'react-router-dom'
import http from '../api/http'

export default function Datasets() {
  const [items, setItems] = useState<any[]>([])
  useEffect(() => {
    http.get('/datasets').then((r) => setItems(r.data))
  }, [])
  return (
    <Row gutter={[16, 16]}>
      {items.length === 0 && (
        <Col span={24}>
          <Card>
            <Typography.Text type="secondary">
              暂无可用数据集，请联系管理员为你所在角色授权。
            </Typography.Text>
          </Card>
        </Col>
      )}
      {items.map((d) => (
        <Col span={8} key={d.id}>
          <Card title={d.title || d.name} hoverable>
            {d.title && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                标识：{d.name}
              </Typography.Text>
            )}
            <Typography.Paragraph type="secondary">{d.description || '（无描述）'}</Typography.Paragraph>
            <Typography.Text>可用字段：{d.columns?.join('、')}</Typography.Text>
            <div style={{ marginTop: 12 }}>
              <Link to={`/datasets/${d.name}`}>进入查询 →</Link>
            </div>
          </Card>
        </Col>
      ))}
    </Row>
  )
}

