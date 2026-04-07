#!/usr/bin/env python3
"""Summarize JSONL run outputs for internal slider CAPTCHA evaluations."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize slider CAPTCHA evaluation runs from JSONL files."
    )
    parser.add_argument(
        "input_path",
        help="Path to a JSONL file or a directory containing JSONL run files",
    )
    parser.add_argument(
        "--group-by",
        default="captcha_version,difficulty_bucket",
        help="Comma-separated bucket fields for grouped metrics",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.85,
        help="Threshold for high-confidence coverage metrics",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the summary JSON",
    )
    return parser.parse_args()


def find_jsonl_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(path for path in input_path.rglob("*.jsonl") if path.is_file())


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        raise ValueError("Cannot compute percentile of an empty list.")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * percentile_value
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = rank - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def normalize_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def load_records(files: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for path in files:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue

                record = json.loads(line)
                try:
                    ground_truth_x = float(record["ground_truth_x"])
                    predicted_x = float(record["predicted_x"])
                except KeyError as exc:
                    raise KeyError(
                        f"{path}:{line_number} is missing required field {exc.args[0]!r}"
                    ) from exc

                normalized = dict(record)
                normalized["_source_file"] = str(path)
                normalized["_abs_error"] = abs(predicted_x - ground_truth_x)
                normalized["_accepted"] = normalize_bool(record.get("accepted"))

                confidence = record.get("confidence")
                normalized["_confidence"] = (
                    float(confidence) if confidence is not None else None
                )
                records.append(normalized)

    return records


def summarize_subset(
    records: list[dict[str, Any]], confidence_threshold: float
) -> dict[str, Any]:
    abs_errors = [record["_abs_error"] for record in records]
    accepted_values = [
        record["_accepted"] for record in records if record["_accepted"] is not None
    ]
    confidence_values = [
        record["_confidence"] for record in records if record["_confidence"] is not None
    ]
    high_confidence = [
        record
        for record in records
        if record["_confidence"] is not None
        and record["_confidence"] >= confidence_threshold
    ]

    summary: dict[str, Any] = {
        "records": len(records),
        "mean_abs_error": round(mean(abs_errors), 4),
        "median_abs_error": round(median(abs_errors), 4),
        "p95_abs_error": round(percentile(abs_errors, 0.95), 4),
    }

    if accepted_values:
        summary["acceptance_rate"] = round(
            sum(1 for value in accepted_values if value) / len(accepted_values), 4
        )

    if confidence_values:
        summary["mean_confidence"] = round(mean(confidence_values), 4)
        summary["high_confidence_threshold"] = confidence_threshold
        summary["high_confidence_coverage"] = round(
            len(high_confidence) / len(records), 4
        )
        if high_confidence:
            high_confidence_errors = [record["_abs_error"] for record in high_confidence]
            summary["high_confidence_mean_abs_error"] = round(
                mean(high_confidence_errors), 4
            )

            accepted_in_high_confidence = [
                record["_accepted"]
                for record in high_confidence
                if record["_accepted"] is not None
            ]
            if accepted_in_high_confidence:
                summary["high_confidence_acceptance_rate"] = round(
                    sum(1 for value in accepted_in_high_confidence if value)
                    / len(accepted_in_high_confidence),
                    4,
                )

    return summary


def build_group_key(record: dict[str, Any], group_fields: list[str]) -> str:
    parts = []
    for field in group_fields:
        parts.append(f"{field}={record.get(field, 'unknown')}")
    return "|".join(parts)


def summarize_records(
    records: list[dict[str, Any]],
    group_fields: list[str],
    confidence_threshold: float,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[build_group_key(record, group_fields)].append(record)

    return {
        "records": len(records),
        "group_fields": group_fields,
        "overall": summarize_subset(records, confidence_threshold),
        "by_bucket": {
            bucket_name: summarize_subset(bucket_records, confidence_threshold)
            for bucket_name, bucket_records in sorted(grouped.items())
        },
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_path)
    files = find_jsonl_files(input_path)

    if not files:
        raise FileNotFoundError(f"No JSONL files found under {input_path}")

    records = load_records(files)
    if not records:
        raise ValueError("No records found in the provided JSONL files.")

    group_fields = [field.strip() for field in args.group_by.split(",") if field.strip()]
    summary = summarize_records(records, group_fields, args.confidence_threshold)
    summary["sources"] = [str(path) for path in files]

    rendered = json.dumps(summary, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote summary to {output_path}")
    else:
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
