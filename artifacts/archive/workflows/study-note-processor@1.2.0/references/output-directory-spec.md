# Obsidian 产物目录规范 (Output Directory Specification)

## 概览

本规范定义学习笔记处理工作流（study-note-processor）产出的 Obsidian 文件在用户 Vault 中的目录结构。所有 MOC 文件与原子笔记的写入路径必须严格遵循此规范。

## 目录结构

```
{vault}                              # 用户 Obsidian Vault 根目录（运行时由用户指定）
└── {course}/                        # 课程/教材目录（如 "高等微积分"）
    ├── 00-{course}-MOC.md           # 书级 MOC（全书知识地图入口）
    ├── _MOCs/                       # 节级 MOC 目录
    │   ├── MOC-X.Y {节名}.md        # 第 X 章第 Y 节的 MOC
    │   └── ...
    └── Atoms/                       # 原子笔记扁平存放目录
        ├── {原子笔记名}.md          # 无子目录，所有原子笔记扁平存放
        └── ...
```

## 文件命名规则

### 书级 MOC

- **路径**：`{vault}/{course}/00-{course}-MOC.md`
- **命名**：`00-{课程名}-MOC.md`
- **示例**：`00-高等微积分-MOC.md`
- **YAML type**：`书MOC`

### 节级 MOC

- **路径**：`{vault}/{course}/_MOCs/MOC-X.Y {节名}.md`
- **命名**：`MOC-{章号}.{节号} {节名}.md`
- **示例**：`MOC-1.1 常数项级数的概念与基本性质.md`
- **YAML type**：`节MOC`
- **注意**：`X.Y` 为章节编号，格式为 `<章>.<节>`；节名不含编号后缀

### 原子笔记

- **路径**：`{vault}/{course}/Atoms/{原子笔记名}.md`
- **命名**：`{学术描述性名称}.md`
- **示例**：`常数项无穷级数定义.md`、`级数收敛的Cauchy准则定理.md`
- **YAML type**：`定义` / `定理` / `性质` / `引理` / `推论` / `方法` / `反例`
- **禁止**：编号化命名（如 `性质 1.1.md`、`Definition 1.md`）

## 双链引用规则

### MOC 中的双链指向

| 引用目标 | 双链写法 | 实际路径 |
|----------|---------|---------|
| 其他节 MOC | `[[MOC-X.Y 节名]]` | `_MOCs/MOC-X.Y 节名.md` |
| 书级 MOC | `[[00-{course}-MOC]]` | `{course}/00-{course}-MOC.md` |
| 原子笔记 | `[[原子笔记名]]` | `Atoms/原子笔记名.md` |

### 原子笔记中的双链指向

- 指向 MOC：`[[MOC-X.Y 节名]]`
- 指向上游原子笔记：`[[上游原子笔记名]]`
- 指向下游原子笔记：`[[下游原子笔记名]]`
- 跨课程引用：Obsidian 路径形式 `[[课程名/Atoms/原子笔记名]]`

### 注意事项

- 双链名使用**文件名（不含扩展名）**，Obsidian 会自动解析
- MOC 文件与 Atoms 文件在不同目录下，可通过扁平双链名互引（Obsidian 自动匹配）
- 书级 MOC 的 H3 标题使用 `[[MOC-X.Y 节名]]` 作为节入口

## 运行时参数

工作流运行时需要用户提供或推断以下参数：

| 参数 | 来源 | 说明 |
|------|------|------|
| `{vault}` | 用户指定 | Obsidian Vault 根目录的绝对路径 |
| `{course}` | 用户指定或从书名推断 | 课程/教材名称 |
| `{X}` | 从 PDF 章号提取 | 章号 |
| `{Y}` | 从 PDF 节号提取 | 节号 |

## 流水线中间产物

> 以下为工作流 Stage 间的落盘传递文件，非 Obsidian 产物，运行结束后可清理。

| 中间产物 | 路径 | 生产者 | 消费者 |
|----------|------|--------|--------|
| PDF 解析结果 | `<work_dir>/.tmp/study-note-processor-<timestamp>/parsed_markdown.md` | s01-pdf-parse | s02-moc-build |

- **`<work_dir>`**：工作流实例运行目录
- **`<timestamp>`**：工作流启动时的 ISO 时间戳（如 `20260513T173000`），用于隔离多次运行
- 此目录下的文件仅用于 Stage 间**大文本落盘传递**（避免上下文 token 消耗），不纳入最终产物

## 与真实 Vault 的对齐

此目录结构对齐以下真实 Obsidian Vault 示例：

> `E:\WorkPlace\Obsidian-工作空间\03_Resources\Math_Foundation\高等微积分\`

该 Vault 中已验证的目录结构：
- `00-高等微积分-MOC.md` — 书级 MOC
- `_MOCs/` — 包含 `MOC-1.1 ... .md` 到 `MOC-5.3 ... .md` 等节 MOC
- `Atoms/` — 扁平存放约 60+ 篇原子笔记
