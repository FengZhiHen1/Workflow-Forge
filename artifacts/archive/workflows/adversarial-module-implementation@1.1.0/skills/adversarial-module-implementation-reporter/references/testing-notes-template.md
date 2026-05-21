# TESTING.md — 测试运行说明

> 自动生成于对抗性验证流程 s08-report 阶段。
> 模块：`{module_id}`

---

## 运行命令说明

### 完整测试套件

```bash
# 主命令（根据技术栈自适应）
{main_test_command}
```

### 单个测试文件（示例）

```bash
{single_file_command_example}
```

### 带覆盖率报告

```bash
{coverage_command}
```

---

## 环境要求

| 项目 | 要求 | 实际环境 |
|:---|:---|:---|
| 语言运行时 | {runtime_version_requirement} | {actual_runtime_version} |
| 包管理器 | {package_manager} | {actual_package_manager_version} |
| 关键依赖 | {key_dependencies} | {installed_versions} |

### 环境初始化步骤

```bash
{env_setup_commands}
```

---

## 已知限制

| # | 限制说明 | 影响范围 | 备注 |
|---|---------|---------|------|
| 1 | {limitation_1_description} | {impact_scope} | {notes} |
| 2 | {limitation_2_description} | {impact_scope} | {notes} |

### 未覆盖场景

- {uncovered_scenario_1}
- {uncovered_scenario_2}

### 外部依赖

- {external_dependency_1}
- {external_dependency_2}

---

## 各轮测试运行记录摘要

| 轮次 | 运行命令 | 通过 | 失败 | 总计 | 状态 |
|:---|:---|---:|---:|---:|:---|
| {round_number} | `{command}` | {passed} | {failed} | {total} | {status} |

---

*本文件由对抗性验证报告生成器自动生成，如有疑问请参考 adversarial-report.md。*
