# Slider CAPTCHA Robustness Workflow

## Use this reference when

- defining a new internal benchmark
- deciding whether an issue is visual, integration, or policy related
- preparing a repeatable review for a challenge revision

## Recommended sequence

1. Confirm safe scope
2. Collect artifacts and metadata
3. Define the manifest and run log schema
4. Benchmark localization
5. Benchmark acceptance in approved environments
6. Bucket failures
7. Recommend mitigations
8. Re-run after fixes

## Decision tree

### If localization is poor

- inspect whether the ground-truth coordinate is aligned to the rendered image
- check image scaling, cropping, DPR, and compression
- compare results by challenge version and background family
- avoid judging the challenge itself until the coordinate path is trustworthy

### If localization is good but acceptance is poor

- suspect integration issues first
- review client/server coordinate transforms
- inspect whether the front end and server disagree on width, offset, or scale
- verify that run logs capture the same coordinate space used for validation

### If localization and acceptance are both strong

- treat the challenge as machine-solvable in its current form
- prioritize remediation instead of further optimizing the attack harness
- benchmark the next revision using the same manifest and metrics

## Review checklist

- raw background assets are retained
- rendered dimensions are logged
- true gap coordinates are available or reconstructable
- challenge version is logged
- browser and DPR are logged
- run metadata includes model or detector version
- failures can be grouped by at least two meaningful bucket fields
- end-to-end runs are limited to approved environments

## Reporting pattern

Start with a one-paragraph conclusion that states whether the challenge appears resilient, weak, or inconclusive. Then include:

- overall metrics
- bucketed regressions
- top failure causes
- recommended next actions

## Warning signs of misleading results

- only screenshots are available and no rendered dimensions are logged
- one aggregate pass rate is reported with no bucket view
- confidence values are logged but never calibrated
- offline error is measured in one coordinate space while acceptance uses another
- improvements are reported without rerunning the same sample set
