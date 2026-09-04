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

- `Market conviction` 28%  
  Captures consensus vs. disagreement signals when volume is high.
- `Recovery momentum` 30%  
  This is a short-window recovery question; whether ship traffic keeps improving over recent days is the core variable.
- `De-escalation durability` 22%  
  If ceasefires and talks only hold briefly, the recovery trend is easy to interrupt.
- `Threshold feasibility` 20%  
  The question is not merely “getting better” but “hitting a clear threshold by month-end,” so whether the bar can be crossed must be assessed on its own.

## Evidence Structuring

- `e1` supports `Returns to normal by Apr 30`  
  Market price ~53.5%, volume ~`$4.4M`, indicating a slight lean toward recovery but still clear disagreement.
- `e2` supports `Returns to normal by Apr 30`  
  Uptick to 20 ships in the last 24 hours, weekend volumes at multi-week highs, plus the two-week ceasefire starting April 7, form the main “recovery momentum” line.
- `e3` supports `Does not return to normal by Apr 30`  
  Throughput still only ~14.6% of normal, war-risk insurance ~16× normal, oil elevated—operating conditions remain fragile.
- `e4` supports `Returns to normal by Apr 30`  
  Talks start April 10; the ceasefire window spans ~two weeks, leaving a path for sentiment and actual flows to improve before month-end.
- `e5` supports `Does not return to normal by Apr 30`  
  Resolution requires `7-day moving average >= 60` on April 30; traffic is still far from that bar, so “threshold feasibility” is the main constraint for the negative case.

## Input Artifact

- Input file: `examples/polymarket_hormuz_geopolitics_input.json`

## Execution

```bash
python3 scripts/rubric_forecast.py \
  --input examples/polymarket_hormuz_geopolitics_input.json \
  --output examples/polymarket_hormuz_geopolitics_output.json
```

- Numeric aggregation, weighting, normalization, and sensitivity analysis were all computed by the script engine.
- See `engine.version` and `engine.input_sha256` in the output JSON for the exact engine version and input hash of this sample.

## Output Summary

- Final answer: `Returns to normal by Apr 30`
- Normalized scores:
  - `Returns to normal by Apr 30`: `0.604172`
  - `Does not return to normal by Apr 30`: `0.395828`
- Sensitivity:
  - Removing `e2` materially narrows the lead, so the current judgment still depends meaningfully on the recovery-momentum narrative.

## Output Artifact

- Output file: `examples/polymarket_hormuz_geopolitics_output.json`
