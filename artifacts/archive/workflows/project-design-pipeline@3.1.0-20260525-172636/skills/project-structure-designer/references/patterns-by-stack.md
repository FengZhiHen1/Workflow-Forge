# 按技术栈的常见项目结构模式参考

> Kimi 在阶段 3（细化模块）时按需读取对应章节，不必全部加载。

---

## 目录

1. [React 前端](#1-react-前端)
2. [Vue 前端](#2-vue-前端)
3. [FastAPI 后端](#3-fastapi-后端)
4. [Node.js / NestJS 后端](#4-nodejs--nestjs-后端)
5. [Go / Gin 后端](#5-go--gin-后端)
6. [全栈 Monorepo](#6-全栈-monorepo)
7. [Python 包组织](#7-python-包组织)

---

## 1. React 前端

### 模式 A：Feature-Based（推荐用于中大型项目）

```
src/
├─ app/                    # 应用入口与全局 providers
├─ features/               # 按业务功能聚合
│  ├─ auth/
│  │  ├─ api.ts            # 该功能域的 API 调用
│  │  ├─ components/       # 仅该功能使用的组件
│  │  ├─ hooks.ts          # 该功能的自定义 Hooks
│  │  ├─ store.ts          # 该功能的状态（若用 Zustand 可拆分）
│  │  ├─ types.ts          # 该功能的类型定义
│  │  └─ index.ts          # 对外暴露的公共接口
│  ├─ notice/
│  └─ activity/
├─ shared/                 # 跨功能复用
│  ├─ components/          # 通用 UI 组件
│  ├─ hooks/               # 通用 Hooks
│  ├─ lib/                 # 工具函数
│  ├─ store/               # 全局状态（如用户会话）
│  └─ types/               # 全局类型
├─ layouts/                # 页面布局壳
├─ pages/ 或 routes/       # 路由配置（若不在 app/ 中）
├─ styles/                 # 全局样式与 CSS 变量
└─ test/                   # 全局测试 setup 与工具
```

**适用**：业务功能清晰、团队规模 > 3 人、长期维护。
**核心优势**：新增功能时所有相关文件在一个目录内，删除功能时一键清理。

### 模式 B：Layer-Based（推荐用于小型/MVP 项目）

```
src/
├─ components/             # 所有组件
├─ pages/                  # 所有页面
├─ hooks/                  # 所有 Hooks
├─ services/               # 所有 API 调用
├─ store/                  # 所有状态
├─ utils/                  # 所有工具
└─ types/                  # 所有类型
```

**适用**：项目规模小、功能少、快速验证。
**核心劣势**：功能扩展后目录膨胀，跨目录依赖混乱。

---

## 2. Vue 前端

### 官方推荐（Vue CLI / Vite）

```
src/
├─ assets/                 # 静态资源
├─ components/             # 通用组件
├─ views/ 或 pages/        # 页面级组件
├─ router/                 # 路由配置
├─ stores/                 # Pinia 状态
├─ composables/            # 组合式函数（Vue 3）
├─ services/ 或 api/       # API 封装
├─ utils/                  # 工具函数
└─ types/                  # 类型定义（TS 项目）
```

### 企业 Feature-Based 扩展

```
src/
├─ modules/
│  ├─ auth/
│  │  ├─ views/
│  │  ├─ components/
│  │  ├─ stores/
│  │  ├─ api/
│  │  └─ index.ts
│  └─ notice/
├─ shared/
│  ├─ components/
│  ├─ composables/
│  └─ utils/
└─ app.ts
```

---

## 3. FastAPI 后端

### 模式 A：三层架构（推荐用于大多数 Web 项目）

```
api-server/
├─ app/
│  ├─ api/
│  │  └─ v1/               # API 版本化
│  │     ├─ auth.py        # Router 层：参数校验、路由定义
│  │     ├─ notices.py
│  │     └─ deps.py        # 依赖注入（DB Session、当前用户）
│  ├─ services/            # 业务逻辑层
│  ├─ repositories/        # 数据访问层（ORM 查询封装）
│  ├─ models/ 或 schemas/  # 若 models 在 py-db 包中，此处仅放 API DTO
│  ├─ middleware/          # 中间件（鉴权、审计、CORS）
│  ├─ core/                # 配置、常量、异常定义
│  └─ main.py              # FastAPI 应用入口
├─ tests/
└─ pyproject.toml
```

**关键规则**：
- Router 禁止直接操作数据库，必须调用 Service。
- Service 禁止直接暴露 HTTP 响应细节，必须返回 DTO/模型。
- Repository 封装所有 ORM 查询，提供租户过滤内置版本。

### 模式 B：Clean Architecture（推荐用于复杂领域/长期演进）

```
api-server/
├─ app/
│  ├─ domain/              # 核心业务实体与规则（纯 Python，无框架依赖）
│  ├─ usecases/            # 应用层：编排领域对象完成业务
│  ├─ interfaces/          # 端口：定义 Repository、Service 接口
│  ├─ infrastructure/      # 适配器：FastAPI Router、SQLAlchemy Repository、Redis Client
│  └─ main.py
```

**适用**：领域模型复杂、多数据源、需要频繁切换基础设施。
**代价**：抽象层多，小团队理解成本高。

---

## 4. Node.js / NestJS 后端

### NestJS 官方模块驱动（推荐）

```
src/
├─ modules/
│  ├─ auth/
│  │  ├─ auth.controller.ts
│  │  ├─ auth.service.ts
│  │  ├─ auth.module.ts
│  │  ├─ dto/
│  │  ├─ entities/
│  │  └─ guards/
│  ├─ notice/
│  └─ activity/
├─ common/                 # 拦截器、过滤器、管道、装饰器
├─ config/                 # 配置模块
├─ database/               # 迁移、种子数据
└─ main.ts
```

### Express / Koa（轻量模式）

```
src/
├─ routes/                 # 路由定义
├─ controllers/            # 请求处理
├─ services/               # 业务逻辑
├─ models/                 # 数据模型（ORM）
├─ middleware/             # 中间件
├─ utils/                  # 工具
└─ config/                 # 配置
```

---

## 5. Go / Gin 后端

### 标准分层（推荐）

```
cmd/
├─ api-server/             # 可执行入口
│  └─ main.go
internal/
├─ domain/                 # 实体与接口
├─ service/                # 业务逻辑
├─ repository/             # 数据访问
├─ handler/                # HTTP Handler（Gin）
├─ middleware/             # 中间件
├─ config/                 # 配置
└─ pkg/                    # 可对外复用的包
pkg/
├─ logger/
├─ validator/
└─ errors/
```

---

## 6. 全栈 Monorepo

### 模式 A：按应用边界分层（推荐用于 Hybrid Monorepo）

```
project-root/
├─ apps/                   # 可执行应用
│  ├─ web-client/          # 前端
│  ├─ api-server/          # 后端 API
│  └─ worker/              # 异步 Worker / 定时任务
├─ packages/               # 共享库
│  ├─ ts-shared/           # 前端共享类型/常量
│  ├─ ts-config/           # 前端共享配置
│  ├─ py-schemas/          # 后端共享 DTO
│  ├─ py-db/               # 后端共享 ORM
│  └─ py-auth/             # 后端共享认证
├─ infrastructure/         # 部署资产（Docker、Nginx、监控）
├─ scripts/                # 启动、迁移、运维脚本
├─ tests/                  # 根级集成/E2E 测试
├─ docs/                   # 架构、接口、部署文档
├─ .env.example
├─ docker-compose.yml
├─ pyproject.toml          # Python workspace
└─ pnpm-workspace.yaml     # Node workspace
```

**关键规则**：
- `apps/` 下每个目录必须能独立构建和运行。
- `packages/` 下只放被多个 app 引用的代码。
- 禁止 app 之间直接 import（通过 packages 共享）。

### 模式 B：按领域分层（Domain-Driven Monorepo）

```
project-root/
├─ domains/
│  ├─ auth/
│  │  ├─ api/              # 该领域的 Router/Controller
│  │  ├─ service/          # 该领域的业务逻辑
│  │  ├─ repository/       # 该领域的数据访问
│  │  ├─ schema/           # 该领域的 DTO
│  │  └─ test/
│  ├─ notice/
│  └─ activity/
├─ shared/                 # 跨领域共享
├─ infrastructure/         # 框架配置、中间件、数据库连接
├─ apps/
│  └─ main.py              # 组装所有领域并启动
```

**适用**：后端领域边界清晰、团队按领域分工。

---

## 7. Python 包组织

### src-layout（推荐用于可安装包）

```
package-name/
├─ src/
│  └─ package_name/
│     ├─ __init__.py
│     └─ module.py
├─ tests/
├─ pyproject.toml
└─ README.md
```

### flat-layout（推荐用于应用/脚本）

```
app-name/
├─ app/ 或 src/
│  ├─ __init__.py
│  └─ main.py
├─ tests/
├─ pyproject.toml
└─ README.md
```

**决策要点**：
- 需要 `pip install -e .` 或发布到 PyPI → src-layout
- 仅作为应用运行，不需要安装 → flat-layout 更简洁
