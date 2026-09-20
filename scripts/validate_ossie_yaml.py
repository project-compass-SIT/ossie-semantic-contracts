"""
validate_ossie_yaml.py

Step 6 check: basic structural validation of OSSIE YAML files, run on every
PR into main before it's allowed to merge. This checks the schema actually
used by your team's converter (top-level `semantic_model` -> `datasets` ->
`fields`, plus `relationships` and `metrics`) — see sample_ossie_model.yml
for a reference file.

This is deliberately light: it catches broken structure and dangling
references fast, in the PR. Snowflake's own verify-only check (run via
deploy_snowflake_semantic_view.py --verify-only, also wired into the PR
workflow) is the deeper structural check against Snowflake itself.

Usage:
    python validate_ossie_yaml.py ossie/
Exits non-zero (fails the Action) if any file is invalid.
"""
import sys
from pathlib import Path

import yaml


def _dialect_expressions(node) -> list[str]:
    if not isinstance(node, dict):
        return []
    return [
        (d.get("expression") or "").strip()
        for d in (node.get("dialects") or [])
        if isinstance(d, dict)
    ]


def validate_model(model: dict, filename: str) -> list[str]:
    errors = []

    name = model.get("name")
    if not name:
        errors.append(f"{filename}: a semantic_model entry is missing 'name'")
        name = "<unnamed>"

    datasets = model.get("datasets") or []
    if not datasets:
        errors.append(f"{filename}: model '{name}' has no datasets")

    dataset_names = set()
    dataset_fields = {}  # dataset_name -> set of field names
    for ds in datasets:
        ds_name = ds.get("name")
        if not ds_name:
            errors.append(f"{filename}: model '{name}' has a dataset missing 'name'")
            continue
        dataset_names.add(ds_name)

        if not ds.get("source"):
            errors.append(f"{filename}: dataset '{ds_name}' missing 'source'")

        fields = ds.get("fields") or []
        if not fields:
            errors.append(f"{filename}: dataset '{ds_name}' has no fields")

        field_names = set()
        for field in fields:
            f_name = field.get("name")
            if not f_name:
                errors.append(f"{filename}: dataset '{ds_name}' has a field missing 'name'")
                continue
            field_names.add(f_name)
            if not _dialect_expressions(field.get("expression")):
                errors.append(
                    f"{filename}: field '{ds_name}.{f_name}' has no usable "
                    f"expression.dialects entry"
                )
        dataset_fields[ds_name] = field_names

        for pk in ds.get("primary_key") or []:
            if pk not in field_names:
                errors.append(
                    f"{filename}: dataset '{ds_name}' primary_key '{pk}' is "
                    f"not one of its fields"
                )

    for rel in model.get("relationships") or []:
        rel_name = rel.get("name", "<unnamed relationship>")
        from_ds, to_ds = rel.get("from"), rel.get("to")
        if from_ds not in dataset_names:
            errors.append(f"{filename}: relationship '{rel_name}' from='{from_ds}' is not a known dataset")
        if to_ds not in dataset_names:
            errors.append(f"{filename}: relationship '{rel_name}' to='{to_ds}' is not a known dataset")

        from_cols = rel.get("from_columns") or []
        to_cols = rel.get("to_columns") or []
        if not from_cols or not to_cols:
            errors.append(f"{filename}: relationship '{rel_name}' missing from_columns/to_columns")
        elif len(from_cols) != len(to_cols):
            errors.append(f"{filename}: relationship '{rel_name}' from_columns/to_columns length mismatch")

        if from_ds in dataset_fields:
            for c in from_cols:
                if c not in dataset_fields[from_ds]:
                    errors.append(f"{filename}: relationship '{rel_name}' from_column '{c}' not in dataset '{from_ds}'")
        if to_ds in dataset_fields:
            for c in to_cols:
                if c not in dataset_fields[to_ds]:
                    errors.append(f"{filename}: relationship '{rel_name}' to_column '{c}' not in dataset '{to_ds}'")

    for metric in model.get("metrics") or []:
        m_name = metric.get("name")
        if not m_name:
            errors.append(f"{filename}: model '{name}' has a metric missing 'name'")
            m_name = "<unnamed>"
        if not _dialect_expressions(metric.get("expression")):
            errors.append(f"{filename}: metric '{m_name}' has no usable expression.dialects entry")

    return errors


def validate_file(path: Path) -> list[str]:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return [f"{path.name}: not valid YAML — {e}"]

    if not isinstance(data, dict):
        return [f"{path.name}: top level must be a mapping"]

    sm = data.get("semantic_model")
    if sm is None:
        return [f"{path.name}: missing required top-level key 'semantic_model'"]

    models = sm if isinstance(sm, list) else [sm]
    if not models:
        return [f"{path.name}: 'semantic_model' is empty"]

    errors = []
    for model in models:
        if not isinstance(model, dict):
            errors.append(f"{path.name}: a semantic_model entry is not a mapping")
            continue
        errors.extend(validate_model(model, path.name))

    return errors


def main():
    if len(sys.argv) != 2:
        print("Usage: validate_ossie_yaml.py <ossie-dir>")
        sys.exit(2)

    ossie_dir = Path(sys.argv[1])
    files = sorted(ossie_dir.glob("*.yaml")) + sorted(ossie_dir.glob("*.yml"))
    if not files:
        print(f"No YAML files found under {ossie_dir}")
        sys.exit(0)

    all_errors = []
    for f in files:
        errs = validate_file(f)
        if errs:
            all_errors.extend(errs)
        else:
            print(f"[OK] {f.name}")

    if all_errors:
        print("\nValidation failed:")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)

    print("\nAll OSSIE YAML files valid.")


if __name__ == "__main__":
    main()
