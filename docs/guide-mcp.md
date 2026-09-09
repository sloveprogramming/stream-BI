# MCP 智能体接入指南

中台通过 MCP（Model Context Protocol）streamable-HTTP 端点向 AI 智能体提供数据查询工具。智能体拿到的数据与 API/Web 完全同一套权限裁剪。

## 端点与鉴权

- 端点：`https://<你的域名>/mcp`（本地调试 `http://127.0.0.1:8000/mcp`）
- 传输：streamable-HTTP（无状态、JSON 响应）
- 鉴权：请求头 `X-API-Key: <key_id>.<secret>`
  - 在 Web 界面「我的 API Key」页创建，或由管理员发放；secret 仅创建时显示一次
  - Key 停用/用户停用立即失效；每次调用记审计日志（channel=mcp）

## 提供的工具

| 工具 | 参数 | 说明 |
|---|---|---|
| `list_datasets` | 无 | 列出当前 Key 有权限的数据集（名称/描述/字段） |
| `describe_dataset` | `name` | 数据集字段清单 + 行级权限规则说明 |
| `query_dataset` | `name`, `filters?`, `limit?` | 查询数据。`filters` 为 `{字段: 值}` 等值过滤；`limit` 默认 50，最大 200 |

返回统一为 `{dataset, columns, rows, total}`；行级权限（如只看华东）与字段白名单（如隐藏成本价）自动生效。

## Dify 配置

1. 工具 → MCP（HTTP 模式）；
2. Server URL 填 `https://<域名>/mcp`，Header 加 `X-API-Key: <key_id>.<secret>`；
3. 刷新工具列表，应看到三个工具；给智能体写系统提示词：
   「你是数据助手，可用工具查询销售/物流数据。回答前先用 describe_dataset 了解字段，再用 query_dataset 查询，引用数据时注明总行数。」

## Coze / 通用 MCP 客户端

任何支持 MCP streamable-HTTP 的客户端均可接入，配置三项：

```json
{
  "url": "https://<域名>/mcp",
  "headers": { "X-API-Key": "<key_id>.<secret>" }
}
```

## 快速自测

```bash
curl -X POST https://<域名>/mcp \
  -H "X-API-Key: <key_id>.<secret>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

返回 200 即端点与鉴权正常。
