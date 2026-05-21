# 契约提取详细算法参考

> 本文档为 `adversarial-module-implementation-init` Skill 的 Step 2（契约提取）和 Step 3（模糊边界显式仲裁）提供详细的程序化解析算法。
> Skill body 中仅保留流程入口，具体解析规则、正则模式、字段映射表、破坏性输入矩阵、模糊边界仲裁规则均定义于此。
> 所属工作流：`adversarial-module-implementation@1.1.0`

---

## 目录

1. [概述与提取目标](#概述与提取目标)
2. [提取来源与优先级](#提取来源与优先级)
3. [模糊边界显式仲裁规则](#模糊边界显式仲裁规则)
4. [步骤 1：解析「输入/输出类型定义」章节](#步骤-1解析输入输出类型定义章节)
5. [步骤 2：将类型定义映射为函数参数契约](#步骤-2将类型定义映射为函数参数契约)
6. [步骤 3：解析「异常处理」章节](#步骤-3解析异常处理章节)
7. [步骤 4：解析「状态机」章节](#步骤-4解析状态机章节)
8. [步骤 5：组装契约条目](#步骤-5组装契约条目)
9. [破坏性输入生成矩阵](#破坏性输入生成矩阵)
10. [输出格式：contract-expectations.md](#输出格式contract-expectationsmd)
11. [契约完整性检查清单](#契约完整性检查清单)
12. [边缘情况与错误处理](#边缘情况与错误处理)
13. [质量检查清单](#质量检查清单)

---

## 概述与提取目标

为每个公开函数/方法/接口提取以下契约信息：

```json
{
  "function_name": "函数名",
  "module": "所属模块",
  "parameters": [
    {
      "name": "参数名",
      "type": "类型声明",
      "required": true,
      "constraints": ["约束1", "约束2"],
      "bounds": {
        "min": null,
        "max": null,
        "regex": null,
        "allowed_values": null
      }
    }
  ],
  "return_type": "返回值类型",
  "exceptions": [
    {
      "type": "异常类型",
      "trigger": "触发条件",
      "contract_reference": "契约条款编号"
    }
  ],
  "preconditions": ["前置条件1"],
  "postconditions": ["后置条件1"],
  "side_effects": ["副作用1"]
}
```

---

## 提取来源与优先级

| 优先级 | 来源 | 章节 | 提取内容 |
|:---:|------|------|------|
| **P0** | 落地规范 | `## 输入/输出类型定义` 或 `## 类型定义` | 参数名、类型声明、必填/可选、默认值、bounds（长度/范围/格式/枚举） |
| **P0** | 落地规范 | `## 异常处理` | 异常类型、触发条件、处理策略 |
| **P1** | 落地规范 | `## 状态机` | 状态转换约束、前置条件、非法转换 |
| **P1** | 设计文档 | `## 接口契约` 或等价章节 | 业务层面输入约束、边界定义、返回值语义 |
| **P2** | 项目结构文档 | 命名规范/模块边界章节 | 命名规范（影响函数名匹配）、模块边界（影响公开接口范围） |

**冲突仲裁规则**：P0 > P1 > P2。同优先级的冲突记录到 `conflict_log`，由 Step 3 仲裁确定。

---

## 模糊边界显式仲裁规则

> **v1.1.0 新增核心要求**：设计文档中所有模糊或不确定的边界描述必须在契约提取阶段显式确定，不得以「约束未声明」搁置。

### 模糊表述识别表

在解析所有来源文档时，遇到以下表述即触发显式仲裁：

| 模糊表述类别 | 典型原文 | 仲裁方向 | 契约中显式写法 |
|:---|:---|:---|:---|
| **非空性模糊** | "通常不为空"、"一般不为空"、"默认非空"、"应避免为空" | 按最严格安全假设 | `required: true`, `constraints: ["禁止 null"]` |
| **可空性模糊** | "可能为 null"、"可为空"、"允许为空" | 若 P0 未明确 → 声明默认值或显式 `required: false` | `required: false, default: null` 或 `required: true` |
| **范围模糊** | "长度不超过大约 N"、"建议长度 X"、"大致在 X-Y 之间" | 取明确边界值 | `bounds: {min: X, max: Y}`（去掉"大约""建议"等修饰词） |
| **分支模糊** | "视情况而定"、"具体视场景"、"依条件而定" | 追溯设计文档中的分支条件表格 | 每个分支单独列为一条契约条目 |
| **行为模糊** | "未定义行为"、"行为未指定"、"未说明" | 按异常处理优先原则 | 显式声明抛出 `RuntimeError` 或返回特定值 |
| **格式模糊** | "仅支持部分格式"、"尽量兼容"、"主流格式" | 列出明确集合 | `bounds.allowed_values: [...]` 或 `bounds.regex: ...` |
| **异常模糊** | "可能报错"、"会出错"、"失败时抛出异常"（无具体类型） | 按技术栈惯例确定 | 显式声明异常类名（如 `ValueError`、`TypeError`） |
| **时序模糊** | "调用前应先..."、"确保已..."（无严格前置条件描述） | 转化为状态约束 | `preconditions: ["状态必须为 X"]` |

### 仲裁优先级

```
1. 若 P0（落地规范）有明确声明 → 直接采用
2. 若 P0 模糊但 P1（设计文档）明确 → 采用 P1，记录仲裁依据
3. 若 P0 和 P1 皆模糊 → 按最严格安全假设仲裁：
   - 非空性：禁止 null > 允许 null
   - 范围性：取最小交集（最严格边界）
   - 异常：必须抛出异常 > 静默处理
   - 状态：前置条件不满足必须拒绝 > 宽容处理
4. 全部来源皆无提及 → 标记为「约束未声明」，但仍需显式写出保守假设
```

### 仲裁记录格式

每条仲裁结果必须在 `contract-expectations.md` 的「冲突记录」或条目备注中留下痕迹：

```markdown
| 编号 | 契约维度 | 原文模糊表述 | 仲裁结果 | 仲裁依据 |
|:---|:---|:---|:---|:---|
| A12 | `create_user` 参数 `email` 非空性 | "通常不为空" | 禁止 null（required: true） | 按最严格安全假设，P0 未明确覆盖 |
```

---

## 步骤 1：解析「输入/输出类型定义」章节

### 1.1 定位目标章节

1. 在落地规范 Markdown 文本中搜索二级标题 `## 输入/输出类型定义` 或 `## 类型定义`
2. 若两者都存在，优先使用 `## 输入/输出类型定义`
3. 若均不存在且 `## 类型定义` 也不存在 → 标记警告"落地规范中未找到类型定义章节"，跳过此步骤，仅从设计文档提取

### 1.2 解析三级标题

在目标章节范围内搜索三级标题 `### {TypeName}`：
- 每个 `###` 标题定义一个新的类型
- TypeName 即类型名，用于后续参数映射

### 1.3 解析字段列表

对每个三级标题下的列表项，应用以下解析规则：

#### 主正则模式

```
^-?\s*\`?(\w+)\`?\s*[:：]\s*(.+)$
  捕获组1: 字段名
  捕获组2: 类型声明及约束描述（完整文本）
```

#### 从捕获组2（完整文本）中进一步提取

| 规则 | 正则 | 提取目标 |
|------|------|------|
| **类型** | 取捕获组2的第一个单词 | 基础类型（`str`, `int`, `float`, `bool`, `List[T]`, `Dict[K,V]`, `Optional[T]`, 自定义类型名） |
| **必填性** | `必填\|\required` → `true`; `可选\|\optional` → `false` | `required`: `true` / `false`（默认 `false`） |
| **默认值** | `默认\s*[:=]\s*([^,\s]+)` | `default`: 捕获值（如 `0`, `"hello"`, `[]`） |
| **长度约束** | `长度\s*(\d+)(?:\s*[-—~到至]\s*(\d+))?` | `bounds`: `{min: N, max: M}` |
| **范围约束** | `范围\s*[\[\(]([^,\s]+)\s*[,，]\s*([^\]\)]+)[\]\)]` | `bounds`: `{min: N, max: M}`（解析开闭区间） |
| **元素数量** | `元素数量\s*(\d+)(?:\s*[-—~到至]\s*(\d+))?` | `bounds`: `{min: N, max: M}`（适用于 `List[T]`） |
| **正则格式** | `格式\s*[:=]\s*(/?.+?/?)` | `bounds.regex`: 正则字符串 |
| **枚举值** | `枚举\s*[:=]?\s*\[(.+?)\]` | `bounds.allowed_values`: 逗号拆分的数组 |

#### 类型归一化

| 原文类型 | 归一化类型 |
|------|------|
| `string`, `str` | `str` |
| `integer`, `int`, `number` | `int` |
| `float`, `double`, `decimal` | `float` |
| `boolean`, `bool` | `bool` |
| `list`, `array`, `List[T]`, `T[]` | `List[T]` |
| `dict`, `Dictionary`, `Dict[K,V]`, `map` | `Dict[K,V]` |
| `None`, `null`, `nil` | `None` |
| 其他大写开头标识符 | 保持原样（自定义类型/枚举） |

### 1.4 模糊边界检测与仲裁（v1.1.0 新增）

在解析每个字段的捕获组2时，扫描模糊表述：

```python
FUZZY_PATTERNS = [
    (r"通常[不为]?空", "非空性模糊"),
    (r"一般[不为]?空", "非空性模糊"),
    (r"默认非空", "非空性模糊"),
    (r"可能为\s*null", "可空性模糊"),
    (r"可为空", "可空性模糊"),
    (r"大约|大概|大致", "范围模糊"),
    (r"建议|推荐", "范围模糊"),
    (r"视情况|依条件", "分支模糊"),
    (r"未定义|未指定|未说明", "行为模糊"),
    (r"部分|尽量|主流", "格式模糊"),
    (r"可能报错|会出错|失败时", "异常模糊"),
]

for pattern, category in FUZZY_PATTERNS:
    if re.search(pattern, capture_group_2):
        trigger_arbitration(field_name, category, original_text)
```

触发仲裁后，根据「模糊边界显式仲裁规则」章节确定显式约束，替换原始模糊描述。

### 1.5 解析示例

**输入 Markdown**：
```markdown
### CreateUserInput
- `username`: str, 必填, 长度 1-100, 格式 /^[a-zA-Z0-9_]+$/
- `email`: str, 必填, 格式 email
- `age`: int, 可选, 默认 0, 范围 [0, 150]
- `tags`: List[str], 可选, 元素数量 0-20
- `role`: UserRole, 必填, 枚举 [ADMIN, EDITOR, VIEWER]
- `nickname`: str, 通常不为空, 长度不超过大约 50
```

**解析结果**（含模糊边界仲裁）：

| 字段名 | 类型 | 必填 | 默认 | bounds | 仲裁说明 |
|:---|:---|:---|:---|:---|:---|
| `username` | `str` | `true` | — | `{min:1, max:100, regex:"/^[a-zA-Z0-9_]+$/"}` | — |
| `email` | `str` | `true` | — | `{regex:"email"}` | — |
| `age` | `int` | `false` | `0` | `{min:0, max:150}` | — |
| `tags` | `List[str]` | `false` | — | `{min:0, max:20}` | — |
| `role` | `UserRole` | `true` | — | `{allowed_values:["ADMIN","EDITOR","VIEWER"]}` | — |
| `nickname` | `str` | `true` | — | `{min:1, max:50}` | 「通常不为空」→ 禁止 null；「大约 50」→ 显式 max=50 |

---

## 步骤 2：将类型定义映射为函数参数契约

### 2.1 映射算法

```
对于 function_signatures.json 中的每个公开函数：
    对于其每个参数：
        1. 在步骤1的类型定义中查找同名字段
           → 找到：直接复用该字段的类型、必填、bounds 信息
        2. 否则，尝试前缀剥离匹配：
           - 若参数名以 "input_" 开头 → 去除前缀后在类型定义中查找
           - 若参数名以 "request_" 开头 → 去除前缀后在类型定义中查找
           - 若参数名以 "payload_" 开头 → 去除前缀后在类型定义中查找
           → 找到：复用信息，标注"通过前缀剥离匹配"
        3. 否则，尝试模糊匹配：
           - 将参数名转为小写，在类型定义字段名（小写）中查找
           - 使用编辑距离 ≤ 2 的模糊匹配
           → 找到：复用信息，标注"通过模糊匹配（编辑距离=N）"
        4. 全部失败：
           → 标记为「约束未声明，采用最宽松假设」
           → 必填参数至少标注 "non-empty" 约束
           → 可选参数标注 "无约束声明"
```

### 2.2 映射结果数据格式

```json
{
  "function_name": "create_user",
  "parameter": "username",
  "source": {
    "type_definition": "CreateUserInput",
    "field": "username",
    "match_method": "direct | prefix_strip | fuzzy | unmatched"
  },
  "type": "str",
  "required": true,
  "default": null,
  "bounds": {"min": 1, "max": 100, "regex": "/^[a-zA-Z0-9_]+$/"},
  "confidence": "high | medium | low",
  "arbitration_note": "模糊边界仲裁说明（如有）"
}
```

### 2.3 匹配置信度

| 匹配方式 | 置信度 | 说明 |
|------|:---:|------|
| `direct` | high | 字段名完全一致，约束信息直接可信 |
| `prefix_strip` | high | 去除常见前缀后匹配，通常可信 |
| `fuzzy` | medium | 编辑距离 ≤2 匹配，需人工复核 |
| `unmatched` | low | 无匹配，标记「约束未声明」 |

---

## 步骤 3：解析「异常处理」章节

### 3.1 定位目标章节

1. 在落地规范 Markdown 中搜索 `## 异常处理`
2. 若不存在 → 标记警告，仅从设计文档提取

### 3.2 表格形式解析

若异常处理以表格形式呈现，提取列：
```
| 触发条件 | 异常类型 | 处理策略 |
| 触发条件 | 异常类型 | 处理策略 | 备注 |
| 错误场景 | 异常类型 | 处理方式 | 重试策略 |
```

对每行：
- **触发条件**：原文保留；若含模糊表述（如"参数无效"未说明具体哪一参数），触发仲裁要求具体化
- **异常类型**：归一化（`ValueError`、`TypeError`、`RuntimeError`、`TimeoutError` 等）
- **contract_reference**：搜索原文中的 `§N.N` 引用，无则按章节顺序分配临时编号（格式 `§{chapter}.{seq}`），标注"原文未编号"

### 3.3 列表形式解析

若异常处理以列表形式呈现，应用正则：

```
^-?\s*(.+?)\s*[:：]\s*抛出\s*(\w+Error)
  捕获组1: 触发条件
  捕获组2: 异常类型
```

扩展模式：
```
^-?\s*(.+?)\s*[:：]\s*(raise|throw)\s*(\w+)
  捕获组1: 触发条件
  捕获组3: 异常类型（需补充 Error 后缀如原始未包含）
```

### 3.4 异常类型归一化表

| 原文写法 | 归一化 | 说明 |
|------|------|------|
| `ValueError` | `ValueError` | Python |
| `TypeError` | `TypeError` | Python |
| `RuntimeError` | `RuntimeError` | Python |
| `KeyError` | `KeyError` | Python |
| `IndexError` | `IndexError` | Python |
| `TimeoutError` / `Timeout` | `TimeoutError` | Python 3.11+ |
| `ValueException` / `值异常` | `ValueError` | 中文/别名 |
| `IllegalArgument` / `参数非法` | `ValueError` | Java → Python |
| `NullPointer` / `空指针` | `TypeError` | Java → Python |
| `HTTPError` / `http 400` | `ValueError`（补充描述 HTTP 状态码） | 自定义 |
| 未识别 | 保留原文字面值 | 标记"未归一化" |

---

## 步骤 4：解析「状态机」章节

### 4.1 定位目标章节

搜索 `## 状态机` 或 `## 状态管理`。不存在 → 跳过此步骤（并非所有模块都需要状态机）。

### 4.2 表格列识别

搜索表格，识别列头：

| 常见列头 | 含义 | 用途 |
|------|------|------|
| `当前状态` / `源状态` / `From State` | 转换前的状态 | 状态约束的左值 |
| `操作` / `事件` / `Event` / `Action` | 触发状态转换的操作 | 函数名匹配 |
| `下一状态` / `目标状态` / `To State` | 转换后的状态 | 状态约束的右值 |
| `前置条件` / `Guard` / `Condition` | 转换前的必须满足条件 | 前置条件约束 |
| `后置条件` / `Effect` | 转换完成后的系统状态 | 后置条件 |
| `备注` / `Note` | 附加说明 | 补充信息 |

### 4.3 约束生成规则

#### 规则 1：前置条件约束

```
IF 前置条件列非空：
  GENERATE: {
    type: "precondition",
    state: "{当前状态}",
    operation: "{操作}",
    precondition: "{前置条件原文}",
    violation_behavior: "前置条件不满足时操作应被拒绝"
  }
```

#### 规则 2：终态约束

```
IF 操作列空缺 或 标记为 "-" / "N/A" / "无" / "终态"：
  GENERATE: {
    type: "terminal_state",
    state: "{当前状态}",
    constraint: "终态下调用任何状态转换操作应抛出异常",
    suggested_exception: "RuntimeError"
  }
```

#### 规则 3：非法直达转换约束

```
FOR EACH 非相邻状态对 (状态A, 状态B)：
  # 非相邻 = 表格中不存在"当前状态=状态A, 下一状态=状态B"的行
  IF 状态A 和 状态B 存在但不直接相邻：
    GENERATE: {
      type: "illegal_transition",
      from_state: "{状态A}",
      to_state: "{状态B}",
      constraint: "从 {状态A} 直接跳转到 {状态B}（不经过中间状态）应被拒绝"
    }
```

#### 规则 4：状态冲突约束

```
IF 某操作在"当前状态"列出现多次（多行有相同的当前状态+操作组合）：
  # 意味着该操作可能在不同条件下产生不同的目标状态
  MARK WARNING: "状态转换存在分支，需检查前置条件是否互斥"
```

---

## 步骤 5：组装契约条目

### 5.1 编号规则

- **格式**：`[A-Z]\d{2,3}`（如 `A01`、`A123`、`B01`）
- **全局唯一**：在整个 `contract-expectations.md` 中无重复编号
- **A 系列**（`A01`, `A02`, ...）：参数/输入约束
- **B 系列**（`B01`, `B02`, ...）：状态/前置条件约束
- **编号上限**：A 系列最大 `A999`，B 系列最大 `B999`

### 5.2 条目生成算法

```
FOR EACH 公开函数 IN function_signatures:
    FOR EACH 参数 IN 函数.parameters:
        IF 参数有 bounds 或 constraints:
            FOR EACH 约束类型 IN 参数.bounds:
                GENERATE 条目:
                    编号: 按 A01, A02, ... 递增
                    契约维度: "{函数名} 参数 {参数名} 的 {约束类型}"
                    破坏性输入: 按破坏性输入矩阵生成（见第 8 章）
                    期望行为: 按约束违反后的预期结果
                    来源章节: 异常/类型定义中的章节引用
                    仲裁备注: 若该参数经历过模糊边界仲裁，记录仲裁依据
        否则 IF 参数.required == true AND 无 bounds:
            GENERATE 条目:
                编号: 按 A01, A02, ... 递增
                契约维度: "{函数名} 参数 {参数名} 非空校验"
                破坏性输入: "None"
                期望行为: "抛出 TypeError"
                来源章节: "类型定义"

    FOR EACH 异常 IN 函数.exceptions:
        GENERATE 条目:
            编号: 按 A 系列递增
            契约维度: "{函数名} 异常场景: {异常.trigger}"
            破坏性输入: "{异常.trigger} 的典型输入"
            期望行为: "抛出 {异常.type}"
            来源章节: 异常章节引用

FOR EACH 状态约束 IN 解析的状态约束列表:
    GENERATE 条目:
        编号: 按 B01, B02, ... 递增
        契约维度: "{函数名} 状态前置条件"
        破坏性输入: "前置条件不满足的场景描述"
        期望行为: "抛出 RuntimeError（或具体异常类型）"
        来源章节: 状态机章节引用
```

### 5.3 条目去重

```python
def deduplicate(entries):
    seen = set()
    deduped = []
    for entry in entries:
        key = (entry["契约维度"], entry["破坏性输入"], entry["期望行为"])
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
        else:
            # 保留来源章节更具体的那条
            replace_if_more_specific(deduped, entry)
    return deduped
```

---

## 破坏性输入生成矩阵

基于参数的约束类型，自动生成对抗性测试可能使用的破坏性输入。

| 约束类型 | 生成的破坏性输入 | 期望行为（通用） |
|:---|:---|:---|
| `required=true` | `None`, `null` (JS), `undefined` (JS) | 抛出 `TypeError` / `ValueError` |
| `type=str, bounds.min>0` | `""`（空字符串）, `"   "`（仅空白）, `"\n\t"` | 抛出 `ValueError` |
| `type=str, bounds.max=N` | `"X" * (N+1)`（超长字符串） | 抛出 `ValueError` |
| `bounds.min` | `bounds.min - 1`（低于最小值） | 抛出 `ValueError` |
| `bounds.max` | `bounds.max + 1`（超过最大值） | 抛出 `ValueError` |
| `bounds.regex` | 生成明确不匹配正则的字符串 | 抛出 `ValueError` |
| `bounds.allowed_values` | 不在枚举列表中的任意值 | 抛出 `ValueError` |
| `type=int` | `NaN`, `Infinity`, `-Infinity`（JS 场景）, `3.14`（浮点数）, `"123"`（数字字符串） | 抛出 `TypeError` |
| `type=float` | `NaN`, `Infinity`, `"abc"` 字符串 | 抛出 `TypeError` |
| `type=bool` | `"true"`（字符串）, `1`（整数）, `"yes"` | 抛出 `TypeError` |
| `type=List[T]` | `[]`（空列表）, `null`/`None`, 超出 bounds 长度的列表 | 按契约：可能返回空列表或抛异常 |
| `type=Dict[K,V]` | `{}`（空字典）, `null`/`None`, 缺少必填 key | 抛异常 |
| 无约束声明（必填） | `None`, `null`, 空值 | 至少抛出通用异常 |
| 无约束声明（可选） | 不生成破坏性输入（边界由测试者按需探测） | — |

### 复合约束的破坏性输入

当参数同时有多个约束时（如 `type=str, required=true, bounds.min=1, bounds.regex=...`），生成破坏性输入时需覆盖**每一种约束的违反场景**。

优先级：先测试类型错误（最基础），再测试值域错误（更具体），最后测试格式错误（最严格）。

---

## 输出格式：contract-expectations.md

### 9.1 文件结构

```markdown
# {ModuleName} 契约期望清单

> **来源**：{落地规范文件名}、{设计文档文件名}、{契约文件列表}
> **冻结时间**：{ISO 8601 时间戳}
> **模块编号**：{module_id}
> **契约条目总数**：{total}（A 系列 {a_count}，B 系列 {b_count}）
> **公开函数数**：{func_count}
> **未覆盖字段数**：{uncovered_count}
> **模糊边界仲裁数**：{arbitration_count}

---

## A 系列：参数/输入约束

| 编号 | 函数 | 参数 | 约束类型 | 破坏性输入 | 期望行为 | 来源 | 仲裁备注 |
|:---|:---|:---|:---|:---|:---|:---|:---|
| A01 | `create_user` | `username` | 非空校验 | `""` (空字符串) | 抛出 `ValueError` | §3.2 | — |
| A02 | `create_user` | `username` | 长度上限 | `"X" * 101` | 抛出 `ValueError` | §3.2 | — |
| A03 | `create_user` | `username` | 格式校验 | `"invalid@name!"` | 抛出 `ValueError` | §3.2 | — |
| A04 | `create_user` | `age` | 范围下限 | `-1` | 抛出 `ValueError` | §3.3 | — |
| A05 | `create_user` | `role` | 枚举约束 | `"SUPERUSER"` | 抛出 `ValueError` | §3.4 | — |
| A06 | `create_user` | `nickname` | 非空校验 | `None` | 抛出 `TypeError` | §3.2 | 原文"通常不为空"→显式禁止null |

---

## B 系列：状态/前置条件约束

| 编号 | 函数 | 当前状态 | 约束描述 | 破坏性输入 | 期望行为 | 来源 | 仲裁备注 |
|:---|:---|:---|:---|:---|:---|:---|:---|
| B01 | `approve_order` | `CANCELLED` | 终态不可操作 | 对 CANCELLED 订单调用 approve | 抛出 `RuntimeError` | §5.2 | — |
| B02 | `ship_order` | `PENDING` | 未审批不可发货 | 跳过审批直接发货 | 抛出 `RuntimeError` | §5.3 | — |

---

## 冲突记录

> 仅当存在冲突时包含此章节

| 冲突维度 | P0（落地规范） | P1（设计文档） | 裁决结果 | 备注 |
|:---|:---|:---|:---|:---|
| `username` 最大长度 | 100 | 50 | 采用 P0：100 | 落地规范优先级更高 |

---

## 模糊边界仲裁记录

> 仅当存在模糊边界仲裁时包含此章节

| 编号 | 契约维度 | 原文模糊表述 | 仲裁结果 | 仲裁依据 |
|:---|:---|:---|:---|:---|
| A06 | `create_user.nickname` 非空性 | "通常不为空" | 禁止 null（required: true） | 按最严格安全假设，P0 未明确覆盖 |

---

## 未覆盖场景

> 仅当存在完全未提及的字段时包含此章节

| 字段 | 函数 | 问题 | 建议 |
|:---|:---|:---|:---|
| `callback_url` | `create_task` | 无类型声明、无格式校验 | 补充 URL 格式校验 |
| `timeout_ms` | `send_request` | 无范围约束（标准建议 1000-60000ms） | 补充范围约束 |
```

### 9.2 文件路径

```
{module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md
```

### 9.3 验证命令

生成后必须运行验证：
```bash
python {workflow_ref_dir}/scripts/validate_contract_expectations.py \
    {contract_path} \
    --function-signatures {function_signatures_path}
```

### 9.4 重新冻结记录追加格式

当从 recontract 分支重新冻结时，在文件头部追加：

```markdown
> **重新冻结记录**：
> - 重新冻结时间：{ISO 8601 时间戳}
> - 本轮更新条目：{affected_ids 列表}
> - recontract 来源：s05-blindtest 第 {round} 轮
> - 更新原因：{recontract_reason}
```

---

## 契约完整性检查清单

提取完成后逐项检查：

- [ ] 每个公开函数都有参数列表
- [ ] 每个参数都有类型声明
- [ ] 每个必填参数都有约束定义（至少 non-empty/non-None）
- [ ] 每个声明的异常都有触发条件
- [ ] 状态机中每个状态转换都有前置条件（或明确标注"无条件"）
- [ ] 编号全局唯一，格式 `[A-Z]\d{2,3}`
- [ ] 每个公开函数至少有一条契约条目
- [ ] 每个必填参数至少有一条破坏性输入
- [ ] 每个异常条件至少有一条对应的期望行为
- [ ] 状态机中非法转换都有对应期望行为
- [ ] 文件头部有来源和冻结时间标注
- [ ] **所有模糊边界已显式仲裁（无"通常""可能""视情况"等词汇残留）**
- [ ] 仲裁记录完整（含原文、结果、依据）

---

## 边缘情况与错误处理

### 11.1 落地规范格式变异

| 情况 | 处理 |
|------|------|
| 章节标题层级不一致（如 `### 输入/输出类型定义` 而非 `##`） | 搜索时允许 ±1 级偏差 |
| 使用 `####` 而非 `###` 定义类型 | 搜索时允许 ±1 级偏差 |
| 表格列顺序不同 | 按列头文本匹配，不依赖列序 |
| 列表使用 `*` 而非 `-` | 两种前缀均接受 |
| 类型声明包含完整包路径（如 `numpy.ndarray`） | 截取最后一个 `.` 后的部分作为类型名 |
| 类型声明使用中文分隔（`string，必填` 而非 `string, 必填`） | 中英文逗号均接受 |
| bounds 区间的括号不对称（如 `[0, 100)` 或 `(0, 100]`） | 正确识别开闭区间 |

### 11.2 提取失败降级

| 情况 | 降级策略 |
|------|------|
| 无「输入/输出类型定义」章节 | 仅从设计文档提取，标记 P0 缺失 |
| 无「异常处理」章节 | 根据类型定义的必填性默认生成非空校验条目 |
| 无「状态机」章节 | 跳过，非所有模块需要状态机 |
| function-signatures.json 不可用 | 从设计文档的函数列表推断公开函数 |
| 某一条目解析失败 | 跳过该条目，记录到 `parse_errors`，继续处理其他条目 |

### 11.3 验证失败修复循环

```
attempt = 0
WHILE attempt < 3:
    result = run validate_contract_expectations.py
    IF result.passed:
        BREAK  // 验证通过
    ELSE:
        FOR EACH error IN result.errors:
            修正 contract-expectations.md 中对应条目
        attempt += 1
IF attempt == 3:
    REPORT ERROR: "契约期望清单验证连续 3 次失败"
    列出无法自动修复的验证错误
```

---

## 质量检查清单

提交 `contract-expectations.md` 前，逐项确认全部满足：

1. [ ] 编号全局唯一，格式 `[A-Z]\d{2,3}`（A 系列和 B 系列各自独立编号）
2. [ ] 每个公开函数至少有一条契约条目
3. [ ] 每个必填参数至少有一条破坏性输入
4. [ ] 每个异常条件至少有一条对应的期望行为
5. [ ] 状态机中的非法转换都有对应的期望行为
6. [ ] 已运行 `validate_contract_expectations.py` 并通过
7. [ ] 文件头部有来源列表和冻结时间标注
8. [ ] 设计文档冲突已按 P0 > P1 > P2 仲裁，结果记录在 conflict_log
9. [ ] **模糊边界已显式仲裁，仲裁记录在「模糊边界仲裁记录」章节中完整列出**
10. [ ] 未覆盖场景已明确列出（`未覆盖场景` 表格），未自行假设
11. [ ] 破坏性输入具体、可执行（非模糊描述如"无效输入"）
12. [ ] 期望行为明确（指定了具体异常类型）
13. [ ] 来源章节引用可追溯（指向落地规范或设计文档的具体章节号）
