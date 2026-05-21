# 工作流目录结构规范

> mathematical-model@2.3.0 全局目录约定。所有 Skill 的产物路径和读取边界统一参照本文档。

---

## 工作空间根布局

```
workspace/
├── shared/                        # GLOBAL_SHARED — 跨小问全局共享
├── .venv/                         # 统一 Python 虚拟环境
├── .agent/                        # 编排器运行时状态
│   ├── workflows/instances/       #   实例存储
│   ├── messages/                  #   消息存储
│   └── backups/                   #   锚点备份
└── problem_N/                     # 每个小问 N 的独立空间
    ├── shared/                    #   PROBLEM_SHARED — 小问内只读共享
    ├── tmp/                       #   PROBLEM_TMP — 小问内临时文件
    ├── MANIFEST.yaml              #   小问级元数据
    └── v{N}/                      #   该小问版本 N 的产物快照
        ├── docs/                  #     VERSION_DOCS — 文档产出
        ├── scripts/               #     VERSION_SCRIPTS — 代码产出
        ├── results/               #     VERSION_RESULTS — 结果产出
        └── VERSION.md             #     该版本的记录
```

---

## 目录速查

| 缩写 | 路径 | 用途 | 读写权限 |
|------|------|------|---------|
| `GLOBAL_SHARED` | `workspace/shared/` | 跨小问共享产物（选题分析、依赖分析等） | 所有 Skill 只读，p0/p1/p5 阶段 Skill 可写 |
| `PROBLEM_SHARED` | `workspace/problem_{N}/shared/` | 单个小问的共享产物 | 该小问的 Skill 只读，问题拆解/数据侦察可写 |
| `PROBLEM_TMP` | `workspace/problem_{N}/tmp/` | 单个小问的临时文件、中间数据 | 该小问的 Skill 可读写 |
| `MANIFEST` | `workspace/problem_{N}/MANIFEST.yaml` | 小问级元数据（版本栈、状态、模型名） | init 可写，其他只读 |
| `VERSION_DOCS` | `workspace/problem_{N}/v{N}/docs/` | 版本 N 的文档产出 | p2/p3/p4/p5 阶段 Skill 可写 |
| `VERSION_SCRIPTS` | `workspace/problem_{N}/v{N}/scripts/` | 版本 N 的代码产出 | code-builder 可写，quality-inspector 只读 |
| `VERSION_RESULTS` | `workspace/problem_{N}/v{N}/results/` | 版本 N 的结果产出 | code-builder 可写，quality-inspector 只读 |
| `VERSION_MD` | `workspace/problem_{N}/v{N}/VERSION.md` | 该版本记录 | init 可写，p5 可追加，其他只读 |
| `.venv/` | `workspace/.venv/` | Python 虚拟环境 | code-builder 管理，其他只读 |

---

## 按 Stage 的读写边界

| Stage | skill_id | 可写目录 | 可读目录 |
|-------|----------|---------|---------|
| p0-init | init | `workspace/`（创建结构）, `GLOBAL_SHARED`, `PROBLEM_SHARED`, `PROBLEM_TMP`, `MANIFEST`, `VERSION_MD` | — |
| p1a-topic-analysis | topic-analyst | `GLOBAL_SHARED` | 附件数据目录 |
| p1b-problem-analysis | problem-decomposer | `PROBLEM_SHARED` | `GLOBAL_SHARED` |
| p1b-data-exploration | data-scout | `PROBLEM_SHARED`, `PROBLEM_TMP` | `PROBLEM_SHARED`, 附件数据 |
| p1c-dependency-analysis | dependency-analyst | `GLOBAL_SHARED` | `GLOBAL_SHARED`, `PROBLEM_SHARED`（所有小问） |
| p2-scheme-design | model-architect | `VERSION_DOCS` | `GLOBAL_SHARED`, `PROBLEM_SHARED` |
| p2-adversarial-review | scheme-reviewer | `VERSION_DOCS` | `VERSION_DOCS`, `PROBLEM_SHARED` |
| p3-math-modeling | math-modeler | `VERSION_DOCS` | `VERSION_DOCS`, `PROBLEM_SHARED` |
| p3-code-core | code-builder | `VERSION_SCRIPTS`, `VERSION_RESULTS`, `.venv/` | `VERSION_DOCS`, `PROBLEM_SHARED` |
| p3-code-extension | code-builder | `VERSION_SCRIPTS`, `VERSION_RESULTS`, `.venv/` | `VERSION_DOCS` |
| p4-validation | quality-inspector | `VERSION_DOCS` | `VERSION_DOCS`, `VERSION_SCRIPTS`, `VERSION_RESULTS` |
| p4-adversarial-review | validation-reviewer | `VERSION_DOCS` | `VERSION_DOCS`, `VERSION_SCRIPTS`, `VERSION_RESULTS` |
| p5-paper-materializer | paper-materializer | `VERSION_DOCS` | `VERSION_DOCS`, `VERSION_RESULTS`, `GLOBAL_SHARED` |

---

## 硬约束

- **禁止写入非己目录**：上表中"可写目录"以外的路径一律禁止写入
- **禁止修改 MANIFEST.yaml / VERSION.md**：除 init 和 p5 外，任何 Skill 不得修改这两个文件
- **路径推导**：脚本中必须使用 `__file__` 推导相对路径，禁止 `os.getcwd()` 硬编码
- **跨小问隔离**：一个小问的 Skill 不得写入另一个小问的 `PROBLEM_SHARED` 或 `PROBLEM_TMP`
