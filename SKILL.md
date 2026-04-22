---
name: "rubric-forecasting"
description: "Use for structured forecasting tasks that require deterministic rubric scoring, calibrated option ranking, explicit evidence tracking, and script-based numeric computation."
---

# Rubric Forecasting Skill

## Goal
Generate topic-specific rubric dimensions around the current forecast, then score, rank, and normalize candidate outcomes against those dynamic dimensions, with a traceable evidence ledger, sensitivity analysis, and structured natural-language reasoning.

## Required Input Contract
Before computing, collect as much as possible of:

- `question`: Clear, verifiable problem statement
- `options[]`: Mutually exclusive, reasonably complete candidate outcomes
- `resolution_rule`: How the outcome will be adjudicated later
- `evidence[]`: Evidence list

Optional fields:

- `forecast_time`: Forecast version timestamp (for logging, not a hard information boundary)
- `close_time`: Target observation/adjudication time (records the question window)
- `rubric_dimensions[]`: Topic-specific rubric dimensions; suggest 3–6, each with:
- `key`
- `label`
- `weight`
- `why_it_matters`
- `base_option_scores`: Baseline scores per option (default all zeros if omitted)
- `normalization_temperature`: Softmax temperature, default `1.0`
- `monitoring_signals[]`: Signals to monitor going forward

If critical fields are missing and cannot be filled, return `insufficient_spec`.

## Non-Negotiable Constraints
- Never perform weighted sums, normalization, or any score arithmetic by hand in LLM text.
- All numeric work must be done by the script engine.
- If the script is unavailable, fails, or output lacks an `engine` field, fail-closed and return `insufficient_spec`.

## Evidence Schema (Per Item)
Each evidence item should include:

- `id`
- `claim`
- `timestamp`
- `source`
- `supports_option` (which option(s); string or array)
- `stance` (`for|against`)
- `strength` (`weak|medium|strong|extreme`)
- `dimension_scores`: Per-dimension scores for this question’s rubric, e.g.:
- `{"incumbent-stability": 0.82, "party-fragmentation": 0.71, "market-conviction": 0.90}`
- `dependency_group` (group for same-source / strongly related evidence)

## Scoring Rules

### 0) Topic-Specific Rubric Design
Before calling the script, produce a topic-specific rubric for the current forecast—not a fixed dimension set.  
Each dimension must specify:

- Why it is directly relevant to this question
- Its relative weight in the overall judgment
- A 0–1 score per evidence item on that dimension

### 1) Strength Factor
- `weak = 0.6`
- `medium = 1.0`
- `strong = 1.6`
- `extreme = 2.2`

### 2) Rubric Weighted Score
Evidence quality:

`quality = sum(weight_i * dimension_score_i)`

The dimension set is not fixed; it is determined dynamically by the forecast question.

### 3) Dependency Penalty
Within the same `dependency_group`, the first item has no penalty; the 2nd and later are multiplied by `0.5`.

### 4) Evidence Contribution
`contribution = quality * strength_factor * dependency_penalty`

- When `stance = for`: add to the target option
- When `stance = against`: subtract from the target option

### 5) Option Aggregation
`raw_score(option) = base_option_score(option) + sum(contribution_i(option))`

### 6) Normalization
Convert raw scores to normalized scores (sum to 1) via softmax:

- `normalized_score(option) = softmax(raw_score / temperature)`

## Engine Execution
Run via script (choose one; from repo root or after `pip install` of this package):

```bash
python scripts/rubric_forecast.py --input /path/to/input.json
```

or:

```bash
python -m rubric_forecast --input /path/to/input.json
```

or (after install):

```bash
rubric-forecast --input /path/to/input.json
```

or:

```bash
cat /path/to/input.json | python -m rubric_forecast
```

## Sensitivity Check (Mandatory)
Run sensitivity at least once:

- Remove the single evidence item with largest absolute contribution, recompute normalized scores
- Emit `delta_top_score`

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
      "label": "Incumbent stability",
      "weight": 0.34,
      "why_it_matters": "Directly affects whether the PM might leave office early."
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
  "reasoning_text": "The real disagreement sits on incumbent stability versus succession pressure. The leading scenario looks like yes because… but no is far from ruled out because… So the fair read is: yes leads slightly; what could flip the call is new evidence on the key dimensions.",
  "rubric_multidim_analysis": {
    "question": "Will product Z ship before 2026-08-01?",
    "rubric_design_summary": "This question uses the following topic-specific rubric dimensions…",
    "method_overview": "This run uses a topic-specific rubric, not a fixed template…",
    "decision_summary": "The top-scoring option is currently yes…",
    "reasoning_narrative": "The key thing to watch is… the evidence leans yes for now… but no is not excluded… overall…",
    "option_analyses": [
      {
        "option": "yes",
        "position": "Leading",
        "score_statement": "Raw score 1.20, normalized 0.62.",
        "dimension_analysis": [
          {
            "dimension": "incumbent-stability",
            "label": "Incumbent stability",
            "assessment": "Positive driver",
            "analysis": "Net contribution on incumbent-stability dimension +0.31…"
          }
        ],
        "evidence_logic": {
          "supporting": ["e1 (for, contribution +1.23): …"],
          "opposing": ["e3 (against, contribution -0.42): …"]
        },
        "risk_note": "Somewhat sensitive to a single key evidence item…"
      }
    ]
  },
  "monitoring_signals": [],
  "assumptions": [],
  "engine": {
    "name": "rubric-forecast-engine",
    "version": "2.2.0",
    "strict_decoupling": true,
    "computed_by": "rubric_forecast.engine",
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
    "computed_by": "rubric_forecast.engine",
    "input_sha256": null
  }
}
```

## Execution Checklist
- Does input satisfy the contract?
- Are time fields for logging/management only (not hard constraints)?
- Was a topic-specific `rubric_dimensions` set produced for this question?
- Does all math come from script output?
- Were evidence correlation and conflicts handled?
- Was sensitivity analysis completed?
- Is natural-language reasoning (`reasoning_text`) present?
- Is structured explanation (`rubric_multidim_analysis`) present?
- Does output include `engine.strict_decoupling = true`?

## Execution Boundary (recommended)

For OpenClaw-integrated unattended execution:

- Keep this skill focused on deterministic forecasting (`run_forecast`) only.
- Put execution authority (topic discovery, feed/adopt/mint, onchain signing) in `lifefun-frontend` server runtime (`/api/autopilot/run`).
- OpenClaw should trigger the frontend webhook/route on schedule; OpenClaw should not hold signing keys.

`rubric-autopilot` remains available for local/offline experiments, but production automation should use the frontend orchestrator.
