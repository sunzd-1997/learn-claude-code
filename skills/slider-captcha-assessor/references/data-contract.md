# Data Contract

## Use this reference when

- defining dataset manifests
- logging model or detector outputs
- standardizing evaluation metrics across runs

## JSONL run record

Each line in a run file should be a single JSON object. Keep the schema lean and stable.

### Required fields

- `sample_id`: stable sample identifier
- `ground_truth_x`: numeric target position in the chosen coordinate system
- `predicted_x`: numeric prediction in the same coordinate system

### Strongly recommended fields

- `captcha_version`: challenge build or family identifier
- `difficulty_bucket`: label such as `easy`, `medium`, `hard`, or a product-specific bucket
- `renderer_bucket`: renderer/browser/DPR bucket
- `confidence`: numeric confidence in `[0, 1]`
- `accepted`: boolean end-to-end verdict when an approved environment run exists
- `run_id`: experiment identifier
- `model_family`: short detector/model family label
- `detector_version`: exact model or pipeline version

### Optional fields

- `environment`: `offline`, `staging`, `shadow`, or another approved environment
- `notes`: brief debugging hint
- `piece_variant`: piece family if applicable
- `background_family`: coarse background type for clustering

## Example record

```json
{
  "sample_id": "v4-mobile-000173",
  "ground_truth_x": 148.0,
  "predicted_x": 151.5,
  "captcha_version": "v4-shadow",
  "difficulty_bucket": "hard",
  "renderer_bucket": "ios-safari-dpr3",
  "confidence": 0.91,
  "accepted": false,
  "run_id": "2026-04-08-small-model-a",
  "model_family": "small-cnn",
  "detector_version": "2026.04.08-1",
  "environment": "shadow"
}
```

## Metric definitions

- `mean_abs_error`: mean of `abs(predicted_x - ground_truth_x)`
- `median_abs_error`: median absolute error
- `p95_abs_error`: 95th percentile absolute error
- `acceptance_rate`: share of records with `accepted == true`
- `high_confidence_coverage`: share of records with `confidence >= threshold`
- `high_confidence_acceptance_rate`: acceptance rate restricted to high-confidence records
- `high_confidence_mean_abs_error`: mean absolute error restricted to high-confidence records

## Bucket guidance

Start with two bucket dimensions:

- `captcha_version`
- `difficulty_bucket`

Add `renderer_bucket` when front-end variability matters. Add `background_family` when visual style matters.

## Coordinate rules

- never mix original-image coordinates with rendered coordinates in the same run file
- if a transform is applied, log or document it alongside the run
- treat missing or ambiguous coordinate-space definitions as a data quality issue

## Analysis guidance

- judge overall metrics and bucketed metrics together
- use confidence-aware metrics instead of forcing every sample through a decision
- compare runs on the same sample set before attributing gains to the model or pipeline
