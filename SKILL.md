---
name: "rubric-forecasting"
description: "Use for structured forecasting tasks that require deterministic rubric scoring, calibrated option ranking, explicit evidence tracking, and script-based numeric computation."
---

# Rubric Forecasting Skill

## Goal
先围绕当前预测话题生成专属 Rubric 维度，再基于这些动态维度对候选结果进行打分、排序与归一化输出，并提供可追溯证据账本、敏感性分析，以及结构化自然语言推理。

## Required Input Contract
在开始计算前，尽量收齐以下字段：

- `question`: 明确、可验证的问题陈述
- `options[]`: 互斥且尽量完备的候选结果
- `resolution_rule`: 未来如何裁决结果
- `evidence[]`: 证据列表

可选字段：

- `forecast_time`: 预测版本时间标签（用于记录，不作为信息边界硬限制）
- `close_time`: 目标观察/裁决时间点（用于记录题目时窗）
- `rubric_dimensions[]`: 话题专属 Rubric 维度定义，建议 3-6 个，每个维度包含：
- `key`
- `label`
- `weight`
- `why_it_matters`
- `base_option_scores`: 选项基础分（不提供时默认全 0）
- `normalization_temperature`: softmax 归一化温度，默认 `1.0`
- `monitoring_signals[]`: 后续监控信号

若缺失关键字段且无法补齐，返回 `insufficient_spec`。

## Non-Negotiable Constraints
- 严禁在 LLM 文本中手工做加权和、归一化、或任何分数运算。
- 所有数值运算必须由脚本引擎完成。
- 若脚本不可用、执行失败、或输出不含 `engine` 字段，必须 fail-closed，返回 `insufficient_spec`。

## Evidence Schema (Per Item)
每条证据建议包含：

- `id`
- `claim`
- `timestamp`
- `source`
- `supports_option`（支持哪个选项，可为字符串或数组）
- `stance`（`for|against`）
- `strength`（`weak|medium|strong|extreme`）
- `dimension_scores`: 针对当前题专属 Rubric 的维度分数，例如：
- `{"incumbent-stability": 0.82, "party-fragmentation": 0.71, "market-conviction": 0.90}`
- `dependency_group`（同源/强相关证据分组）

## Scoring Rules

### 0) Topic-Specific Rubric Design
在调用脚本前，先根据当前预测题生成话题专属 Rubric，而不是套用固定维度。  
每个维度需要说明：

- 这个维度为何与该题直接相关
- 该维度在总判断中的相对权重
- 每条证据在这个维度上的 0-1 分数

### 1) Strength Factor
- `weak = 0.6`
- `medium = 1.0`
- `strong = 1.6`
- `extreme = 2.2`

### 2) Rubric Weighted Score
证据质量分：

`quality = sum(weight_i * dimension_score_i)`

这里的维度集合不是固定的，而是由当前预测题动态决定。

### 3) Dependency Penalty
同一 `dependency_group` 的首条证据不惩罚；第 2 条及以后乘以 `0.5`。

### 4) Evidence Contribution
`contribution = quality * strength_factor * dependency_penalty`

- 当 `stance = for`：对目标选项加分
- 当 `stance = against`：对目标选项减分

### 5) Option Aggregation
`raw_score(option) = base_option_score(option) + sum(contribution_i(option))`

### 6) Normalization
将原始分通过 softmax 转成归一化分（和为 1）：

- `normalized_score(option) = softmax(raw_score / temperature)`

## Engine Execution
必须通过脚本执行：

```bash
python scripts/rubric_forecast.py --input /path/to/input.json
```

或：

```bash
cat /path/to/input.json | python scripts/rubric_forecast.py
```

## Sensitivity Check (Mandatory)
至少执行一次敏感性分析：

- 移除绝对贡献值最大的单条证据后重算归一化分
- 输出 `delta_top_score`

## Output Format (JSON First)
```json
{
  "status": "ok",
  "final_answer": "option_x",
  "raw_option_scores": [{"option": "x", "score": 1.27}],
  "normalized_scores": [{"option": "x", "score": 0.61}],
  "rubric_dimensions": [
    {
      "key": "incumbent-stability",
      "label": "执政稳定性",
      "weight": 0.34,
      "why_it_matters": "直接影响首相是否可能提前离任。"
    }
  ],
  "normalization_temperature": 1.0,
  "evidence_ledger": [
    {
      "id": "e1",
      "supports_option": "x",
      "stance": "for",
      "strength": "strong",
      "quality": 0.72,
      "contribution": 1.152,
      "dimension_scores": {
        "incumbent-stability": 0.88
      }
    }
  ],
  "conflict_resolution_notes": [],
  "sensitivity": [
    {
      "drop_evidence_id": "e1",
      "delta_top_score": -0.18
    }
  ],
  "reasoning_text": "这道题真正的分歧点，落在执政稳定性和继任压力。眼下更像主场景的是 yes，因为...不过 no 也远没有出局，因为...所以更合适的读法是：yes 小幅领先，真正可能改变判断的，是后续在关键维度上的新增证据。",
  "rubric_multidim_analysis": {
    "question": "Will product Z ship before 2026-08-01?",
    "rubric_design_summary": "本题采用的话题专属 Rubric 维度为...",
    "method_overview": "本题使用话题专属 Rubric，而非固定维度模板...",
    "decision_summary": "当前最高分选项是 yes...",
    "reasoning_narrative": "这道题最需要盯住的是...当前更支持 yes...但 no 并没有被排除...综合来看...",
    "option_analyses": [
      {
        "option": "yes",
        "position": "领先",
        "score_statement": "原始分 1.20，归一化得分 0.62。",
        "dimension_analysis": [
          {
            "dimension": "incumbent-stability",
            "label": "执政稳定性",
            "assessment": "正向驱动",
            "analysis": "执政稳定性维度净贡献+0.31..."
          }
        ],
        "evidence_logic": {
          "supporting": ["e1（支持，贡献+1.23）: ..."],
          "opposing": ["e3（压制，贡献-0.42）: ..."]
        },
        "risk_note": "对单条关键证据较敏感..."
      }
    ]
  },
  "monitoring_signals": [],
  "assumptions": [],
  "engine": {
    "name": "rubric-forecast-engine",
    "version": "2.2.0",
    "strict_decoupling": true,
    "computed_by": "scripts/rubric_forecast.py",
    "input_sha256": "..."
  }
}
```

## Failure Modes
```json
{
  "status": "insufficient_spec",
  "missing_fields": [],
  "blocking_reasons": [],
  "next_required_inputs": [],
  "engine": {
    "name": "rubric-forecast-engine",
    "version": "2.0.0",
    "strict_decoupling": true,
    "computed_by": "scripts/rubric_forecast.py",
    "input_sha256": null
  }
}
```

## Execution Checklist
- 是否满足输入契约？
- 时间字段是否仅用于记录与管理（而非硬限制）？
- 是否先为当前题生成了 topic-specific `rubric_dimensions`？
- 是否所有数学计算都来自脚本输出？
- 是否处理证据相关性与冲突？
- 是否完成敏感性分析？
- 是否输出自然语言推论过程（`reasoning_text`）？
- 是否输出 `rubric_multidim_analysis` 结构化自然语言解释？
- 是否包含 `engine.strict_decoupling = true`？
