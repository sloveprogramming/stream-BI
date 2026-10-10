import { Handle, Position, type NodeProps } from '@xyflow/react'
import { summarize, useFlowStore, type OpNode } from '../../store/flowStore'

export default function OperatorNode({ data, selected }: NodeProps<OpNode>) {
  const datasetNames = useFlowStore((s) => s.datasetNames)

  // 必填项没填就在画布上标红 —— 别等点了运行才发现
  const required = data.schema.required ?? []
  const missing = required.filter((k) => data.params[k] === undefined || data.params[k] === '')

  const cls = [
    'flow-node',
    selected ? 'is-selected' : '',
    missing.length ? 'is-invalid' : '',
    data.isSource ? 'flow-node--source' : '',
    data.isSink ? 'flow-node--sink' : '',
    data.engine === 'python' ? 'flow-node--python' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={cls}>
      {!data.isSource && <Handle type="target" position={Position.Left} className="flow-handle" />}
      <div className="flow-node__head">
        <span className="flow-node__title">{data.title}</span>
        {data.engine === 'python' && <span className="flow-node__badge">AI</span>}
      </div>
      <div className="flow-node__sub">
        {missing.length ? `待填：${missing.join(', ')}` : summarize(data.opType, data.params, datasetNames)}
      </div>
      {!data.isSink && <Handle type="source" position={Position.Right} className="flow-handle" />}
    </div>
  )
}
