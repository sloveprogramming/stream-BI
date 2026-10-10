import { Collapse, Tooltip } from 'antd'
import type { DragEvent } from 'react'
import type { OperatorSpec } from '../../api/flows'

/**
 * 左侧算子面板：按 category 分组，可拖到画布。
 *
 * 面板内容完全来自后端 `/flows/operators`（含每个算子的 JSON Schema），
 * 所以**后端加一个算子，这里自动出现**，前端不用改。
 */
export default function OperatorPanel({ operators }: { operators: OperatorSpec[] }) {
  const groups = new Map<string, OperatorSpec[]>()
  for (const op of operators) {
    const list = groups.get(op.category) ?? []
    list.push(op)
    groups.set(op.category, list)
  }

  const onDragStart = (event: DragEvent<HTMLDivElement>, opType: string) => {
    event.dataTransfer.setData('application/bisql-operator', opType)
    event.dataTransfer.effectAllowed = 'move'
  }

  return (
    <div className="flow-panel">
      <div style={{ fontWeight: 600, marginBottom: 8 }}>算子库</div>
      <Collapse
        size="small"
        defaultActiveKey={[...groups.keys()]}
        items={[...groups.entries()].map(([category, list]) => ({
          key: category,
          label: `${category}（${list.length}）`,
          children: (
            <div>
              {list.map((op) => (
                <Tooltip key={op.type} title={op.description} placement="right" mouseEnterDelay={0.4}>
                  <div
                    className="flow-operator-item"
                    draggable
                    onDragStart={(e) => onDragStart(e, op.type)}
                    title={op.description}
                  >
                    <div style={{ flex: 1 }}>
                      <div>
                        {op.title}
                        {op.engine === 'python' && (
                          <span className="flow-node__badge" style={{ marginLeft: 6 }}>
                            AI
                          </span>
                        )}
                      </div>
                      <div className="flow-operator-item__desc">{op.type}</div>
                    </div>
                  </div>
                </Tooltip>
              ))}
            </div>
          ),
        }))}
      />
    </div>
  )
}
