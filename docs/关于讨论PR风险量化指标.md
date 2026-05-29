# 关于讨论 PR 风险量化指标

## 问题背景

当前 `compute_priority_score()` 的评分公式对"小改动但高风险路径"的文件打分偏低。

以 `backend/auth/service.py`（80 行新增）为例，实际评分为 63，但 spec 预期为 70-90。

## 评分明细

| 组件 | 计算方式 | 值 | 权重 | 贡献 |
|------|----------|-----|------|------|
| path_risk | `compute_path_risk_score()` + hint_boost | 100 | 0.45 | 45.0 |
| change_size | `(additions + deletions) // 5` | 16 | 0.30 | 4.8 |
| file_status | `_FILE_STATUS_SCORES["modified"]` | 40 | 0.15 | 6.0 |
| lang_risk | `_LANG_FAMILY_RISK["backend"]` | 70 | 0.10 | 7.0 |
| **合计** | | | | **62.8 → 63** |

## 根因分析

`change_size` 使用 `// 5` 计算，80 行改动仅得 16 分。该组件以 0.30 的权重将总分往下拉了约 27 分。

核心矛盾：**change_size 是绝对量指标，而 path_risk 是静态风险指标**。当文件改动量小但路径高风险时（如 auth、payment），change_size 会严重拖低总分，导致高风险文件被低估。

## 解决方案

### 方案 A：调整 change_size 公式

将 `// 5` 改为 `// 3`：

```python
def compute_change_size_score(additions: int, deletions: int) -> int:
    return min(100, (additions + deletions) // 3)
```

- 80 行 → 26 分（原 16 分）
- 总分：45 + 7.8 + 6 + 7 = 65.8 → 66
- 仍未达到 70，需继续调参

**优点**：改动小，影响全局
**缺点**：对所有文件统一提分，未针对高风险路径做特殊处理

### 方案 B：高风险路径加保底分

当 `path_risk >= 80` 时，最终分数不低于 `path_risk * 0.7`：

```python
score = (
    path_risk * 0.45
    + change_size * 0.30
    + file_status * 0.15
    + lang_risk * 0.10
)
# 高风险路径保底
if path_risk >= 80:
    score = max(score, path_risk * 0.7)
return min(100, max(0, round(score)))
```

- path_risk=100 时，保底 70 分
- path_risk=90 时，保底 63 分
- 不影响普通文件的计算

**优点**：直接保证高风险路径文件分数不会被 change_size 拖垮
**缺点**：引入了条件分支，公式不再纯粹是加权平均

### 方案 C：组合方案

降低 `// 5` 为 `// 3`，同时加保底：

```python
def compute_change_size_score(additions: int, deletions: int) -> int:
    return min(100, (additions + deletions) // 3)

# 评分时
score = (
    path_risk * 0.45
    + change_size * 0.30
    + file_status * 0.15
    + lang_risk * 0.10
)
if path_risk >= 80:
    score = max(score, path_risk * 0.7)
return min(100, max(0, round(score)))
```

## 待讨论

- [ ] 选择哪个方案？
- [ ] 保底系数 0.7 是否合适？是否需要根据 path_risk 值分档？
- [ ] 是否需要更新 spec 中的预期分数范围？
- [ ] 其他边界 case（如 docs/README.md 应该多少分）是否需要验证？
