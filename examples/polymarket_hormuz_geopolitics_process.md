# Strait of Hormuz Forecast Example Process

## Scenario

- Topic: `Will Strait of Hormuz traffic return to normal by April 30, 2026?`
- Snapshot date: `2026-04-07`
- Market source: [Polymarket Geopolitics](https://polymarket.com/geopolitics)
- Event source: [Polymarket Event](https://polymarket.com/event/strait-of-hormuz-traffic-returns-to-normal-by-april-30)

## Why This Market Was Chosen

- It is a geopolitics market with real disagreement rather than a one-sided price.
- The market combines shipping data, ceasefire durability, diplomacy, and a hard resolution threshold.
- That makes it a good example for topic-specific rubric design.

## Rubric Design

- `市场定价强度` 28%
  用来吸收高成交量下的市场共识与分歧信号。
- `恢复动能` 30%
  这题是短窗口恢复题，最近几天船流是否持续改善是核心变量。
- `缓和持续性` 22%
  停火和谈判如果只能维持很短时间，恢复趋势容易被打断。
- `达标可行性` 20%
  这题不是“是否好转”，而是“是否在月底前达到明确阈值”，所以必须单独评估门槛能否被跨过。

## Evidence Structuring

- `e1` 支持 `Returns to normal by Apr 30`
  市场价格约 53.5%，成交约 `$4.4M`，说明市场略偏恢复，但仍然分歧明显。
- `e2` 支持 `Returns to normal by Apr 30`
  最近 24 小时回升到 20 艘，周末量创多周高点，叠加 4 月 7 日启动的两周停火，构成“恢复动能”主线。
- `e3` 支持 `Does not return to normal by Apr 30`
  当前吞吐量仍只有常态的 14.6%，战争保险约为常态 16 倍，油价高位，说明运行环境仍然脆弱。
- `e4` 支持 `Returns to normal by Apr 30`
  4 月 10 日开启谈判，停火窗口大约覆盖两周，意味着月底前仍有情绪与实际流量继续改善的路径。
- `e5` 支持 `Does not return to normal by Apr 30`
  裁决要求 4 月 30 日时 `7-day moving average >= 60`，当前距离阈值仍远，说明“达标可行性”是反方主约束。

## Input Artifact

- Input file: [polymarket_hormuz_geopolitics_input.json](/Users/meng/Downloads/prediction_skill/lite/examples/polymarket_hormuz_geopolitics_input.json)

## Execution

```bash
python3 /Users/meng/Downloads/prediction_skill/lite/scripts/rubric_forecast.py \
  --input /Users/meng/Downloads/prediction_skill/lite/examples/polymarket_hormuz_geopolitics_input.json \
  --output /Users/meng/Downloads/prediction_skill/lite/examples/polymarket_hormuz_geopolitics_output.json
```

- Numeric aggregation, weighting, normalization, and sensitivity analysis were all computed by the script engine.
- Engine version: `2.2.0`
- `input_sha256`: `36a9b130e8715a1818b997b3cb24e224e27ec214cb04578bc3af5a998342464b`

## Output Summary

- Final answer: `Returns to normal by Apr 30`
- Normalized scores:
  - `Returns to normal by Apr 30`: `0.604172`
  - `Does not return to normal by Apr 30`: `0.395828`
- Sensitivity:
  - Removing `e2` materially narrows the lead, so the current judgment still depends meaningfully on the recovery-momentum narrative.

## Output Artifact

- Output file: [polymarket_hormuz_geopolitics_output.json](/Users/meng/Downloads/prediction_skill/lite/examples/polymarket_hormuz_geopolitics_output.json)
