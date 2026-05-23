# 项目结构设计文档输出模板

本参考文件定义 `项目名称-项目结构.md` 的精确结构与质量标准。

## 文档结构

```markdown
# [项目名称] - 项目结构设计

> 生成日期：YYYY-MM-DD
> 技术栈方案：[项目名称-技术栈设计.md]
> 架构模式：[选定的架构模式]

---

## 一、技术栈回顾

用 3-5 行摘要回顾技术栈方案的关键选型，为后续结构设计提供上下文：

| 维度 | 选型 | 对结构的影响 |
|:---|:---|:---|
| 前端框架 | [选型] | [目录范式、组件组织方式] |
| 后端语言/框架 | [选型] | [包组织方式、入口文件约定] |
| 数据存储 | [选型] | [数据访问层组织] |
| 架构模式 | [选型] | [顶层分层策略] |
| 部署方式 | [选型] | [是否需要 Docker/K8s/CI 配置目录] |

---

## 二、选定的结构模式

记录步骤 2 的模式选择结果与理由：

```markdown
| 维度 | 选定模式 | 选择理由 | patterns-by-stack.md 章节 |
|:---|:---|:---|:---|
| 前端 | [如 React Feature-Based] | [中大型项目、团队 >3 人、长期维护] | §1 模式 A |
| 后端 | [如 FastAPI 三层架构] | [标准 Web 项目、领域复杂度适中] | §3 模式 A |
| Monorepo | [如按应用边界分层] | [前后端分离、共享类型/配置库] | §6 模式 A |
| Python 包 | [如 src-layout] | [需 pip install -e . 开发安装] | §7 |
```

> 若某维度不适用（如非 Monorepo 项目），标注"不适用"即可。
```

---

## 三、分层架构

### 3.1 分层总览

```markdown
| 层级 | 层名 | 职责 | 典型内容 | 技术组件 |
|:---:|:---|:---|:---|:---|
| L1 | 表示层 | 用户界面与交互 | 页面、组件、路由 | [前端框架] |
| L2 | 应用层 | 业务用例编排 | Service、DTO、Middleware | [后端框架] |
| L3 | 领域层 | 核心业务逻辑 | Entity、ValueObject、Repository接口 | — |
| L4 | 基础设施层 | 外部依赖适配 | Repository实现、API Client、Cache | [数据库驱动]、[消息队列] |
| L5 | 公共层 | 跨层共享 | 类型定义、工具函数、常量 | — |
```

### 3.2 层间依赖规则

用 Mermaid 图或文字描述层间允许的依赖方向：

```
表示层 → 应用层 → 领域层 ← 基础设施层
                  ↑
              公共层（所有层可引用）
```

### 3.3 各层详解

对每一层展开：
- **职责边界**：该层负责什么、不负责什么
- **包含内容**：具体的文件/目录类型
- **对外接口**：该层向上暴露什么、向下依赖什么
- **技术映射**：该层使用的具体技术栈组件

---

## 四、目录骨架

### 4.1 完整目录树

```
project-root/
├── src/
│   ├── presentation/        # L1 表示层
│   │   ├── pages/           #   页面入口（路由级别）
│   │   ├── components/      #   可复用 UI 组件
│   │   └── layouts/         #   布局组件
│   ├── application/         # L2 应用层
│   │   ├── services/        #   用例编排服务
│   │   ├── dto/             #   数据传输对象
│   │   └── middleware/      #   请求级中间件（认证、日志）
│   ├── domain/              # L3 领域层
│   │   ├── entities/        #   业务实体
│   │   ├── value-objects/   #   值对象
│   │   └── repositories/    #   仓储接口（仅接口定义）
│   ├── infrastructure/      # L4 基础设施层
│   │   ├── persistence/     #   数据库实现（ORM、Migration）
│   │   ├── external/        #   外部 API 客户端
│   │   └── cache/           #   缓存实现
│   └── shared/              # L5 公共层
│       ├── types/           #   共享类型/接口定义
│       ├── utils/           #   纯函数工具
│       └── constants/       #   全局常量
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/
├── scripts/
└── [框架配置文件]
```

> 目录树应根据实际技术栈和步骤 2 选定的结构模式调整。示例为 Clean Architecture 通用分层模板。若选定模式为 React Feature-Based、FastAPI 三层、Go 标准分层等，应使用 `references/patterns-by-stack.md` 中对应模式的目录结构替换此示例。

### 4.2 目录职责速查表

| 目录 | 所属层 | 职责 | 依赖方向 |
|:---|:---|:---|:---|
| `src/presentation/` | L1 | UI 渲染与用户交互 | 可引用 L2、L5 |
| `src/application/` | L2 | 用例编排与流程控制 | 可引用 L3、L5 |
| `src/domain/` | L3 | 核心业务规则 | 仅可引用 L5 |
| `src/infrastructure/` | L4 | 外部系统适配 | 实现 L3 定义的接口，可引用 L5 |
| `src/shared/` | L5 | 跨层共享基础设施 | 不被任何层依赖（仅被引用） |

---

## 五、层间数据流

选取 2-3 个代表性业务场景，描述数据在各层之间的流转。

### 5.1 场景一：[名称]

```
用户请求 → L1(接收HTTP请求、参数校验)
         → L2(调用应用服务、组装上下文)
         → L3(执行领域逻辑、返回Entity)
         → L2(组装DTO)
         → L1(渲染响应)
```

数据形态变化：`HTTP Request → Command/Query DTO → Domain Entity → Response DTO → HTTP Response`

### 5.2 场景二：[名称]

[重复格式]

---

## 六、包/模块组织约定

### 6.1 命名规范

| 元素 | 命名风格 | 示例 |
|:---|:---|:---|
| 目录 | [kebab-case / snake_case] | `user-profile/` |
| 文件 | [风格] | `user-service.ts` |
| 类/接口 | [PascalCase] | `UserService` |
| 函数/变量 | [camelCase / snake_case] | `getUserById` |
| 常量 | [UPPER_SNAKE_CASE] | `MAX_RETRY_COUNT` |

### 6.2 文件组织规则

- 每个文件只导出一个主要的类/函数/组件
- 通过 barrel 文件（`index.ts` / `__init__.py`）统一对外暴露
- 测试文件与被测文件同目录或镜像到 `tests/` 目录
- 禁止跨层 import（基础设施层 import 表示层等）

### 6.3 模块边界

若为 monorepo：
- 各 package 的 `package.json` / `pyproject.toml` 明确声明依赖
- 共享代码通过 npm workspace / pip editable install 引用
- 禁止 package 间循环依赖

---

## 七、扩展预留

为未来可能引入但尚未在技术栈方案中确定的技术，预留目录位置：

| 预留位置 | 预期用途 | 触发条件 |
|:---|:---|:---|
| `src/infrastructure/queue/` | 消息队列适配 | 异步任务需求确认后 |
| `src/infrastructure/search/` | 全文搜索引擎适配 | 搜索需求确认后 |
| `docker/` | 容器化配置 | 正式部署前 |

> 扩展预留仅创建空目录（含 `.gitkeep`），不预先编写代码。

---

## 附录 A：技术栈映射速查

| 技术栈组件 | 所属层 | 对应目录 |
|:---|:---|:---|
| [逐一列出技术栈方案中的每个组件] | | |

## 附录 B：设计决策记录

记录结构设计过程中的关键决策：
- 为什么选择此分层策略而非其他？
- 为什么某些目录合并而非拆分？
- 哪些决策是受技术栈约束而做出的？
