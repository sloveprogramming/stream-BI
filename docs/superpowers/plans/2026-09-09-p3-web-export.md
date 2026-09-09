# P3 Web 用户端与文件导出实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供 React Web 界面（用户自助查数/导出/管理 Key；管理员配置权限与监控）与文件导出能力（在线导出 + 定时报表下载中心），全部复用 P2 权限裁剪。

**Architecture:** 后端在 P2 单体上扩展：JWT 登录（PyJWT）提供 Web 会话，与 API Key 两条鉴权通道并存（同一 `get_principal` 语义）；导出与定时报表经 `build_scoped_sql` 裁剪后用 openpyxl/csv 生成文件；报表由 APScheduler 定时生成到 `exports/` 目录。前端 Vite + React + TS + AntD SPA，生产由 FastAPI 静态托管。

**Tech Stack:** 后端新增 PyJWT、openpyxl；前端 React 18 + TypeScript + Ant Design 5 + React Router + Axios（Vite 构建）。

**Spec:** docs/superpowers/specs/2026-09-08-data-distribution-platform-design.md（§4.4 文件分发、§4.6 Web 界面）

## Global Constraints

- 沿用 P1/P2 全部约束（backend/ 目录、/api/v1 前缀、标识符校验、TDD、每任务 commit）。
- Web 端所有数据接口复用 P2 权限链路（数据集授权/行级/字段级），禁止在视图层绕过 `build_scoped_sql`。
- JWT：HS256，secret 走 `BISQL_JWT_SECRET`（.env），有效期 `BISQL_JWT_EXPIRE_HOURS` 默认 12h；只存散列（口令 PBKDF2 沿用 P2）。
- 导出上限 `BISQL_EXPORT_MAX_ROWS` 默认 50000 行，超出报 400。
- 报表文件存 `backend/exports/`（gitignore），下载需鉴权且只能下载自己的文件。
- 前端代码位于 `frontend/`；`npm run build` 产物 `frontend/dist`；后端 `StaticFiles` 挂载到 `/`（SPA fallback 到 index.html）；API 路由不受影响。
- 新依赖加入 `pyproject.toml`；前端依赖锁定 `package-lock.json`。

## File Structure（P3 新增）

```
backend/app/
├── security/jwts.py          # create_token / decode_token
├── api/auth_api.py           # POST /auth/login, GET /auth/me
├── api/deps.py               # + get_web_principal（Bearer JWT → Principal）
├── api/export_api.py         # GET /datasets/{name}/export
├── reports/models.py         # ReportTask / ReportRun（并入 models/access.py 亦可）
├── reports/service.py        # generate_report_file（裁剪→openpyxl/csv→文件）
├── reports/scheduler.py      # refresh_report_jobs（APScheduler）
├── api/reports_api.py        # admin: report-tasks CRUD；user: my/reports
frontend/                      # Vite + React + TS + AntD
```

---

### Task 1: JWT 登录与会话

**Interfaces:**
- Produces: `security/jwts.create_token(user_id, username, is_admin) -> str`、`decode_token(token) -> dict`（过期/非法抛 `jwt.InvalidTokenError`）；`get_web_principal(request, ctx)`（Authorization: Bearer → 401/403）；`POST /api/v1/auth/login` `{username,password}` → `{token, user:{id,username,display_name,is_admin}}`（密码错 401；停用 403）；`GET /api/v1/auth/me`
- Test: `backend/tests/unit/test_jwts.py`、`backend/tests/unit/test_auth_api.py`

要点代码：

```python
# security/jwts.py
import jwt  # PyJWT
from app.settings import get_settings


def create_token(user_id: int, username: str, is_admin: bool) -> str:
    s = get_settings()
    payload = {
        "sub": str(user_id), "username": username, "is_admin": is_admin,
        "exp": datetime.now(timezone.utc) + timedelta(hours=s.jwt_expire_hours),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
```

```python
# deps.py 追加
@dataclass
class WebPrincipal:  # 与 Principal 字段一致语义，user 来自 JWT
    user: object


def get_web_principal(request: Request, ctx: Context = Depends(get_ctx)) -> WebPrincipal:
    from app.models import User
    from app.security.jwts import decode_token

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing token")
    try:
        payload = decode_token(auth.removeprefix("Bearer "))
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")
    with ctx.session_factory() as session:
        user = session.get(User, int(payload["sub"]))
        if user is None or not user.enabled:
            raise HTTPException(status_code=403, detail="user disabled")
        return WebPrincipal(user=user)
```

Settings 新增：`jwt_secret: str = "dev-secret-change-me"`、`jwt_expire_hours: int = 12`。

- [ ] Step 1 失败测试（token 往返/过期拒绝；登录成功/密码错 401/停用 403/me）→ Step 2 实现 → Step 3 `python -m pytest tests/unit/test_jwts.py tests/unit/test_auth_api.py -v` 全过 → Step 4 commit `feat: JWT 登录与会话`

### Task 2: 在线导出（CSV/XLSX）

**Interfaces:**
- Produces: `GET /api/v1/datasets/{name}/export?format=csv|xlsx` → FileResponse（`Content-Disposition`）；无授权 403；行数超 `export_max_rows` → 400；写审计 `action=export`
- Test: `backend/tests/unit/test_export_api.py`

要点：复用 `build_scoped_sql`（不带 limit）+ `fetchmany(export_max_rows+1)` 判断超限；xlsx 用 `openpyxl.Workbook(write_only=True)`；csv 用 `io.StringIO` + `codecs.iterencode`/直接 `Response`；文件名 `f"{dataset}_{timestamp}.csv|xlsx"`（RFC 5987 中文可省略，用 ASCII 名）。

- [ ] TDD 四步 + commit `feat: 在线导出 CSV/XLSX（权限裁剪+行数上限）`

### Task 3: 定时报表与下载中心

**Interfaces:**
- Produces:
  - 模型 `ReportTask(id, name, dataset_id FK, format, cron, filters JSON, owner_user_id FK, enabled, created_at)`、`ReportRun(id, task_id FK, file_name, row_count, status, error, created_at)`
  - `reports/service.generate_report_file(task) -> ReportRun`（裁剪查询→写 `exports/`→落库）
  - `reports/scheduler.refresh_report_jobs(scheduler, session_factory)`（P1 模式：`report_{id}`，replace_existing，max_instances=1）
  - API：`POST/GET/DELETE /api/v1/admin/report-tasks`（管理员）；`GET /api/v1/my/reports`（当前用户 runs 倒序）；`GET /api/v1/my/reports/{run_id}/download`（仅本人文件）
  - `create_app` lifespan 中注册 `refresh_report_jobs`
- Test: `backend/tests/unit/test_reports.py`（service 生成+权限；API 权限边界；scheduler 注册/移除）

- [ ] TDD 四步 + commit `feat: 定时报表与下载中心`

### Task 4: 前端骨架（Vite + React + TS + AntD）

**Files:** `frontend/`（Vite 模板裁剪：src/api/http.ts axios 实例（Bearer 拦截器+401 跳登录）、src/pages/Login.tsx、src/layouts/MainLayout.tsx（AntD Layout+Sider 菜单：数据集/我的报表/我的Key/(管理员)用户管理/数据集授权/同步任务/审计日志）、src/router.tsx、App.tsx、vite.config.ts（proxy /api → 127.0.0.1:8000））

验收：`npm install && npm run build` 成功；`npm run dev` 登录页可用。

- [ ] commit `feat: 前端骨架（Vite+React+AntD+登录布局）`

### Task 5: 用户端页面

- 数据集列表页（GET /datasets，卡片/表格）→ 点进查询页（AntD Table 服务端分页，筛选表单按列生成，导出按钮调 export API）；
- 我的 Key 页（列表+创建弹窗显示一次 secret+复制+撤销）；
- 我的报表页（runs 表格 + 下载按钮）。
- [ ] commit `feat: 用户端页面（查数/导出/Key/报表）`

### Task 6: 管理端页面

- 用户管理（Table+新建表单+发Key弹窗）；
- 数据集与授权（注册表单；授权编辑器：选角色+数据集→字段 checkbox 白名单→行级规则构建器（字段/操作符/值三列，可增删行））；
- 同步任务管理（列表+水位线+手动触发按钮）；
- 审计日志（Table+按数据集筛选）。
- [ ] commit `feat: 管理端页面（用户/授权/同步/审计）`

### Task 7: 静态托管与 P3 端到端验收

- `main.py`：`app.mount("/", StaticFiles(directory="frontend/dist", html=True))`（目录缺失时跳过，避免后端单测依赖前端构建）；SPA fallback：404 时返回 index.html（仅非 /api 路径）。
- e2e：登录→建数据集授权→Web token 查询→导出 csv→创建报表任务→手动触发生成→下载，全链路断言。
- `npm run build` + pytest 全量回归 + commit + `git tag p3-done`

---

## Self-Review

1. Spec 覆盖：§4.4 文件分发=Task 2/3；§4.6 Web 界面两端口=Task 5/6；会话=Task 1。
2. 前端任务为结构规格（组件清单+接口绑定），实现遵循 AntD 标准模式，无逻辑歧义。
3. 类型一致：`generate_report_file(task)->ReportRun`、`refresh_report_jobs(scheduler, session_factory)` 与 P1 scheduler 模式一致；`get_web_principal` 与 `get_principal` 并存且互不影响。
