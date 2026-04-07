---
name: slider-captcha-assessor
description: Assess self-owned slider or puzzle CAPTCHA systems in internal staging, shadow, or offline replay environments. Use when Codex needs to design a defensive evaluation harness, define datasets and labels, review image-localization pipelines, measure localization and acceptance metrics, cluster hard cases, or recommend mitigations for variable-gap slider CAPTCHA robustness. Do not use for third-party targets or live bypass requests.
---

# Slider Captcha Assessor

Design and review internal robustness evaluations for self-owned slider CAPTCHA systems. Prefer offline replay, dataset-driven measurement, and remediation guidance over live interaction with production services.

## Guardrails

- Require self-owned artifacts, test accounts, or explicit internal authorization.
- Prefer staging, shadow, or offline replay environments.
- Reframe requests for a "solver" or "95%+ pass rate" into a robustness benchmark and hardening exercise.
- Separate visual localization from end-to-end acceptance. Treat sustained machine success against your own challenge as evidence that the challenge needs improvement.
- Refuse or redirect any request involving third-party services, live abuse, or unsanctioned bypassing.

## Workflow

### 1. Establish the evaluation surface

- Confirm what artifacts exist: raw background image, piece image if used, true gap coordinate, rendered size, browser/device pixel ratio, challenge version, server verdict, and any non-sensitive bucketing fields.
- If artifacts exist only as screenshots, call out the risks introduced by CSS scaling, recompression, or viewport transforms.
- Load `references/data-contract.md` before proposing manifests, logs, or run outputs.

### 2. Choose the right workstream

- Use an offline benchmark when the goal is to measure localization quality or compare model variants.
- Use an integration review when the likely issue is coordinate mapping, scaling, canvas rendering, or client/server mismatch.
- Use failure analysis when a system passes aggregate metrics but specific versions, backgrounds, or renderers fail.
- Use remediation planning when the team needs concrete design changes after a weak challenge is identified. Load `references/remediation-playbook.md`.

### 3. Create or refresh the workspace

- Run `scripts/bootstrap_eval_tree.py <target-dir>` to create a consistent folder structure for samples, manifests, runs, and reports.
- Keep immutable raw artifacts separate from derived manifests and model outputs.
- Preserve run metadata per experiment: model family, detector version, environment, browser, and challenge build/version.

### 4. Measure in layers

- Measure localization first: MAE, median absolute error, and P95 absolute error.
- Measure confidence behavior next: high-confidence coverage and high-confidence quality.
- Measure end-to-end acceptance last, and only in approved environments.
- Use `scripts/summarize_runs.py <path-to-jsonl>` to aggregate results overall and by bucket.
- Load `references/workflow.md` if you need a checklist or decision tree for which metric to trust first.

### 5. Analyze hard cases

- Bucket misses by challenge version, difficulty bucket, renderer, browser/DPR, compression mode, and background family.
- Compare model confidence with actual error. Low-confidence abstentions are useful signal, not automatic failures.
- Highlight clusters where small visual changes or one renderer cause disproportionate misses.

### 6. Recommend mitigations

- Prefer multi-signal verification over single-image dependence.
- Review whether the system leaks stable coordinate cues, relies on deterministic rendering, or overweights terminal position over richer behavioral evidence.
- Rank mitigations by expected security impact, implementation cost, and regression risk.

## Output Checklist

Produce the following unless the user explicitly asks for less:

- A scoped evaluation plan with assumptions and environment boundaries
- The dataset/log contract and required fields
- A run summary with overall and bucketed metrics
- The top failure clusters or integration issues
- Recommended hardening steps with priority and rationale

## Resource Map

- `references/workflow.md`: end-to-end evaluation flow and decision points
- `references/data-contract.md`: sample manifest, run log schema, and metric definitions
- `references/remediation-playbook.md`: defensive remediation patterns and prioritization
- `scripts/bootstrap_eval_tree.py`: create a repeatable workspace layout
- `scripts/summarize_runs.py`: compute overall and bucketed metrics from run JSONL

## Common Requests

Use this skill when the user asks for tasks such as:

- "Audit whether our new slider challenge is too easy for machine vision."
- "Design an offline benchmark for a variable-gap slider CAPTCHA."
- "Summarize why version `v4-shadow` passes globally but fails on mobile Safari."
- "Help us structure labels, run logs, and metrics for a CAPTCHA robustness review."

Chinese prompt example:

`使用 $slider-captcha-assessor 帮我为自研滑块验证码设计一套离线鲁棒性评测方案，要求包含数据标注、分桶指标、失败样本回流和加固建议。`

