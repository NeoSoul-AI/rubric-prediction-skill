# Rubric Forecasting Skill Introduction

## What this is

This is a skill for **structured forecasting**.

Its goal is not to have the model spit out a probability on the fly in conversation, but to split prediction into two parts:

1. The model understands the question, organizes evidence, and designs appropriate evaluation dimensions for the current question.
2. The script engine performs all numeric work: weighting, aggregation, normalization, and sensitivity analysis.

In the end it produces two kinds of results:

1. Auditable numeric output.
2. Readable natural-language reasoning.

So you get not only “what the answer is,” but also “why that answer.”

## What problem it solves

When a plain LLM predicts directly, you often see:

1. Plausible-sounding text with wrong intermediate scores.
2. Forcing different topics into the same analytic frame.
3. Ignoring counterevidence or treating repeated sources as independent evidence.
4. Long prose that is hard to replay to see why the conclusion followed.

This skill is designed to address that:

1. Judgment dimensions match the question, not a fixed template.
2. Math is delegated to scripts, not to LLM mental arithmetic.
3. Each piece of evidence’s role is traceable.
4. Final output has both numbers and an analyst-style reasoning narrative.

## Key concepts

These terms are where newcomers most often get stuck.

1. `question`
   The forecasting question itself.
   Example: `Will Strait of Hormuz traffic return to normal by April 30, 2026?`

2. `options`
   The candidate outcomes for this question.
   Example: `["yes", "no"]`
   They should be mutually exclusive so only one can ultimately hold.

3. `resolution_rule`
   The rule for how the outcome will be scored later.
   Think of it as the grading rubric.
   Example: `If the company officially announces on its website, count as yes; otherwise no.`

4. `evidence`
   A list of evidence—what you rely on when judging.
   Each item should ideally state:

   - What it claims
   - Where it comes from
   - Which option it supports
   - How strong it is
   - How it scores on each rubric dimension for this question

5. `rubric_dimensions`
   Question-specific “judgment dimensions.”
   Not a one-size-fits-all template, but designed for the topic at hand.

   Examples:

   - For conflict questions: e.g. durability of de-escalation, escalation risk, feasibility of meeting the bar
   - For elections: e.g. candidate base, funding momentum, media agenda
   - For product launches: e.g. execution readiness, organizational commitment, time pressure

6. `dimension_scores`
   Per-dimension scores for a piece of evidence, usually in `0`–`1`.
   Interpret as “how much this evidence supports on this dimension.”

7. `strength`
   Evidence strength.
   The script supports four levels:

   - `weak`
   - `medium`
   - `strong`
   - `extreme`

8. `dependency_group`
   Marks evidence that is similar or may share a root source or information chain.
   The script will not count redundant information many times.

9. `normalization_temperature`
   Temperature for softmax normalization.
   Most users can leave the default `1.0`.

10. `forecast_time`
    When this forecast version was produced.
    Used for logging, versioning, and review—not a hard information boundary.

11. `close_time`
    How long you intend to observe the question.
    Used to record the forecast window for later review.

12. `fail-closed`
    A safety posture: if input is incomplete, the script fails, or the result is untrusted, the system prefers failure over a falsely confident answer.

13. `insufficient_spec`
    Means “the input spec is not enough to proceed.”
    The system should tell you:

    - What is missing
    - Why computation cannot continue
    - What to supply next

14. `engine`
    A computational signature in the result.
    Shows the output was produced by the script, not invented by the model on the spot.

15. `strict_decoupling=true`
    Means semantic reasoning and numeric computation are strictly separated:

    - The model interprets and explains
    - The script does the numbers

## Minimal input example

A minimal workable input:

```json
{
  "question": "Will product Z ship before 2026-08-01?",
  "options": ["yes", "no"],
  "resolution_rule": "Official release note before 2026-08-01 00:00:00-07:00 counts as yes.",
  "rubric_dimensions": [
    {
      "key": "execution-readiness",
      "label": "Execution readiness",
      "weight": 0.40,
      "why_it_matters": "This question hinges on whether the product is close to shippable."
    },
    {
      "key": "organizational-commitment",
      "label": "Organizational commitment",
      "weight": 0.35,
      "why_it_matters": "Whether leadership is genuinely driving launch materially affects the outcome."
    },
    {
      "key": "timeline-pressure",
      "label": "Timeline pressure",
      "weight": 0.25,
      "why_it_matters": "Closer deadlines amplify the impact of any slip."
    }
  ],
  "evidence": [
    {
      "id": "e1",
      "claim": "Supplier confirms pilot run",
      "source": "supplier-report",
      "supports_option": "yes",
      "stance": "for",
      "strength": "strong",
      "dimension_scores": {
        "execution-readiness": 0.85,
        "organizational-commitment": 0.65,
        "timeline-pressure": 0.70
      }
    }
  ]
}
```

## How it works

The end-to-end flow is eight steps.

1. Clarify the question
   Collect the core inputs:

   - `question`
   - `options`
   - `resolution_rule`
   - `evidence`

2. Design a question-specific rubric
   Infer the question type, then pick roughly 3–6 dimensions that matter most.

3. Structure evidence
   For each item specify:

   - Which side it supports
   - Strength
   - Scores on each dimension
   - Whether it belongs to a dependency group

4. Script: per-evidence contribution
   The script combines:

   - Dimension weights
   - Dimension scores
   - Evidence strength
   - Dependency penalty
   to get each evidence item’s contribution to options.

5. Script: aggregate to options
   Contributions are summed per option to get raw scores.

6. Script: normalize
   Raw scores become normalized scores that sum to 1 for comparison across options.

7. Script: sensitivity analysis
   Drop the single most influential evidence item and recompute to see if the conclusion shifts materially.
   This tests robustness.

8. Output “numbers + commentary”
   Final output includes scores and a natural-language analysis.

## What it outputs

On success, key fields include:

1. `final_answer`
   The option best supported right now.

2. `raw_option_scores`
   Raw score per option.

3. `normalized_scores`
   Normalized score per option.

4. `evidence_ledger`
   Evidence ledger: per item you can see:

   - What it supports
   - Strength
   - Quality score
   - Final contribution

5. `sensitivity`
   Sensitivity results: how much the top option’s score moves if the largest contributor is removed.

6. `reasoning_text`
   Continuous natural-language reasoning—an “analyst note” that typically covers:

   - Where the real disagreement lies
   - The leading scenario
   - Why the counter-scenario is not ruled out
   - Which variables are most likely to change the call

7. `rubric_multidim_analysis`
   Finer structured explanation:

   - Method overview
   - Decision summary
   - Multi-dimensional diagnosis per option
   - Key supporting evidence
   - Risk notes

8. `engine`
   Computational signature proving script-engine provenance.

On failure:

1. `status = insufficient_spec`
2. `missing_fields`
3. `blocking_reasons`
4. `next_required_inputs`
5. `engine`

## What is `reasoning_text`?

`reasoning_text` is a forecast commentary.

It usually follows this shape:

1. Name the real point of contention
2. Describe the leading scenario
3. Explain why the counter-scenario still lives
4. Call out variables that could flip the judgment

So it is not only “what the scores are,” but an analyst-style explanation of:

- What the market (or debate) is really fighting over
- Why you lean one way for now
- What props up the other side
- What to watch to revise the call

## Design rationale

Core idea: let the model and the engine each do what they are good at.

1. Model: semantics

   - Understand the question
   - Design a question-specific rubric
   - Organize evidence
   - Explain results

2. Script: numerics

   - Weighted quality scores
   - Evidence contributions
   - Option aggregation
   - Normalization
   - Sensitivity analysis

3. Protocol: guardrails

   - Missing input → `insufficient_spec`
   - Script unavailable → fail-closed
   - No `engine` field → do not treat as a valid numeric result

This split reduces the usual mess when one model must both analyze and compute.

## Why it works

It works not because it is “more complex,” but because it is more stable.

1. Dimensions follow the question
   Different question types emphasize different variables.

2. Evidence paths are traceable
   Every item shows whom it moved and by how much.

3. Correlated sources are down-weighted
   The same fact said three ways is not three independent strong signals.

4. Anti-fragility check
   Sensitivity shows whether the call leans too hard on one item.

5. Output is readable and auditable
   Good for a quick read and for replay/audit.

## Why it is often more accurate than a raw LLM forecast

“More accurate” comes from error-structure improvement, not magic.

1. The frame fits the question
   Dimensions are designed for this item, not a generic template.

2. Mental math errors are removed
   LLMs slip on multi-step weighting, normalization, and sign handling; scripts do not.

3. Narrative-compute bias is reduced
   Raw LLM forecasts often favor “sounds smooth” over “adds up.”
   Here: compute first, then explain.

4. Counter-scenarios stay visible
   They are not washed out because the main story reads better.

5. Easier to replay
   Change one evidence item, weight, or timestamp and see how the conclusion moves.

Note:
This improves process and numeric reliability; it does not replace high-quality sources.
Garbage-in still yields weak forecasts—no magical fix.

## Why it saves tokens

The win is moving arithmetic out of the chat.

1. No long calculation traces in model text
   Scoring, summing, and normalization live in the script.

2. Stable output shape
   The model does not reinvent a format every time.

3. Cheap iteration
   Change one evidence row or weight; the script recomputes without another “mental pass.”

4. Explanations stay focused
   Tokens go to “why this judgment,” not to redoing formulas.

## When to use it

Better fits:

1. Forecasts with clear, adjudicable outcomes.
2. Settings that need reproducible, auditable output.
3. When you need both numeric results and an explanation trail.

Poor fits:

1. Pure value questions.
2. Questions with no definable resolution rule.
3. Evidence that cannot be structured at all.

## One-line summary

This skill cleanly separates **question understanding** from **numeric computation**:

1. The model understands the question, organizes evidence, and generates explanation.
2. The script scores, normalizes, and runs sensitivity analysis.

So it tends to be steadier, more auditable, and more token-efficient than asking an LLM to “just predict” in prose.
