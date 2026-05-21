# G1-G12 趋绿规则表

> 由 `scripts/detect_green_seeking.py` 扫描执行。扫描通过标准：`toxicity_score ≤ 2`。
>
> 阻断级规则（G9/G11/G12）单次命中即导致扫描失败，无论总分是否 ≤2。
> 非阻断级规则累计 toxicity_score 超过 2 也视为不通过。

## 规则总表

| Rule | 说明 | 权重 | 阻断级 | 修复方向 |
|:---|:---|:---|:---|:---|
| **G1** | 异常吞咽 — 裸 `except: pass` / `except Exception: pass` 吞掉所有异常，无法区分契约期望异常与实现漏洞 | 3 | 否 | 改为 `pytest.raises(具体异常类型, match="消息模式")`，让异常显式传播 |
| **G2** | 构造-断言 — `assert` 出现在被测函数调用之前，或测试中根本无被测调用 | 3 | 否 | 确保 `assert` 位于被测函数调用之后，验证的是被测函数的行为而非测试自身的构造 |
| **G3** | 宽泛断言 — `assert result in (a, b, c)` 备选值 > 2，或 `assert a or b or c` 放宽通过条件 | 1 | 否 | 改为精确匹配契约要求的异常消息关键词或具体返回值 |
| **G4** | 纯存在性断言 — 断言仅 `is not None` / `len(result) > 0` / `assert True`，不与具体契约条款关联 | 2 | 否 | 替换为基于契约条款的具体值断言（如 `assert result == expected`）或异常断言 |
| **G5** | 标准库测试 — 测试直接验证 Python 标准库/语言内置行为（如 `assert str(123) == "123"`），无被测函数调用 | 2 | 否 | 删除不调用被测函数的测试，或补充被测函数调用作为测试主体 |
| **G6** | 纯 Mock 验证 — 仅使用 `mock.assert_called_once_with()` / `assert_called()` 等 Mock 验证，无对被测函数返回值或副作用的业务断言 | 2 | 否 | 补充对被测函数返回值/副作用的具体业务断言，Mock 验证仅作为辅助 |
| **G7** | 自我赋值断言 — 赋值后立即断言同一变量（如 `x = 1; assert x == 1`），被测函数调用缺失于赋值与断言之间 | 3 | 否 | 确保被测函数调用发生在赋值与断言之间，断言的是被测函数产生的值 |
| **G8** | 恒真式欺骗 — `or True` / `and False` 等逻辑恒真式。典型：`assert exc_info.value is not None`（`pytest.raises` 上下文管理器已保证非 None） | 3 | 否 | 删除恒真/恒假断言，替换为对异常类型/消息/被测返回值的具体断言 |
| **G9** | 防御性跳过 — `if condition: return` / `if not available: pass` 等条件分支绕过测试逻辑。包括因"模块可能不存在"的防御性导入包裹 | 3 | **是** | 移除所有 `if condition: return/pass` 模式，改为完整执行测试逻辑。让异常自然抛出，由上游 Stage 分类记录 |
| **G10** | 核心接口偏离 — 测试未调用被测核心业务函数（如测试全是 `isinstance` 类型检查、标准库操作，或 Mock target 指向被测模块自身函数/类） | 2 | 否 | 补充对被测核心业务函数的调用；若使用 Mock，确保 target 仅为外部依赖并在注释中标注 `# external-dep-mock: {服务名}` |
| **G11** | 空测试 — 函数体无 `assert` / `pytest.raises` / `pytest.fail()` / `unittest.TestCase.fail()`。仅含注释、变量赋值或 `pass` | 3 | **是** | 补充 `assert` 或 `pytest.raises`，或删除无意义测试 |
| **G12** | 内部函数导入 — `from sut import _internal_func` 从被测模块导入 `_` 前缀函数/类 | 3 | **是** | 删除 `from sut import _xxx`，改为通过公开接口的返回值/副作用间接验证内部行为 |

## 扫描结果模型

```json
{
  "toxicity_score": 0,
  "is_clean": true,
  "suspects": [
    {
      "rule_id": "G1",
      "location": "test_file.py:42",
      "description": "裸 except: pass 吞掉所有异常",
      "weight": 3
    }
  ]
}
```

- `toxicity_score`：所有 suspects 的 weight 之和
- `is_clean`：`toxicity_score ≤ 2` 且无阻断级规则命中
- 阻断级规则（`G9`/`G11`/`G12`）命中时，`is_clean` 强制为 `false`，无论 `toxicity_score` 值

## 使用方式

在 SKILL.md 的自检流程中引用本规则表。测试代码生成或修正后，运行：

```bash
python scripts/detect_green_seeking.py \
    {test_file} \
    --sut-module {被测模块顶层包名} \
    --output {dir}/green-seeking-report.json
```

不通过时按本表"修复方向"列逐项修正，重新扫描直至通过。**不得在未通过扫描的情况下输出测试代码。**
