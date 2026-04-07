# Remediation Playbook

## Use this reference when

- the benchmark suggests the challenge is machine-solvable
- one challenge revision is much weaker than the rest
- localization is consistently easy across diverse backgrounds

## Pattern: strong localization across most buckets

Signals:

- low absolute error overall
- low error even on visually busy backgrounds
- results are stable across versions or renderers

Response:

- review whether the challenge exposes stable visual cues that align too directly with the target coordinate
- reduce deterministic rendering patterns and version skew
- avoid relying on one image-level cue as the dominant signal

## Pattern: strong localization but weak acceptance

Signals:

- offline error is low
- accepted runs are inconsistent
- one browser or DPR bucket fails disproportionately

Response:

- audit client/server coordinate transforms
- normalize width, offset, and scale handling
- verify that validation uses the same coordinate system captured during benchmarking

## Pattern: weakness concentrated in one version

Signals:

- one challenge build has much better machine success than others
- failures or wins cluster around a recent asset or rendering change

Response:

- patch or retire the weak version first
- rerun the same benchmark after the fix
- avoid averaging away a weak version with stronger siblings

## Pattern: high machine success with simple replay or standardized interaction

Signals:

- strong acceptance does not depend on nuanced interaction differences
- end-to-end success tracks terminal position more than richer context

Response:

- combine image challenge results with additional server-side risk signals
- review whether acceptance criteria overweight the final coordinate and underweight broader evidence
- prefer layered risk decisions over one challenge result

## Prioritization rubric

Rank each proposed fix on three axes:

- expected security impact
- implementation cost
- regression risk

Start with fixes that reduce repeatable machine success while keeping support and accessibility obligations intact.
