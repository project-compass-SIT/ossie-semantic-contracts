"""
validate_ossie_yaml.py

Step 6 check: basic structural validation of OSSIE YAML files, run on every
PR into main before it's allowed to merge. This is intentionally light —
it checks the file parses and has the sections OSSIE expects, and that
every relationship/measure references an entity that actually exists.
Snowflake's own `verify_only` check (run inside deploy_snowflake_semantic_view.py
with --verify-only) is the deeper structural check; this one just catches
obvious mistakes fast, in the PR, before anything gets deployed.

Usage:
    python validate_ossie_yaml.py ossie/
Exits non-zero (fails the Action) if any file is invalid.
"""
import sys
from pathlib import Path

import yaml

REQUIRED_TOP_LEVEL = ["entities"]
OPTIONAL_TOP_LEVEL = ["relationships", "measures", "calculations", "annotations"]


def validate_file(path: Path) -> list[str]:
    errors = []
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return [f"{path.name}: not valid YAML — {e}"]

    if not isinstance(data, dict):
        return [f"{path.name}: top level must be a mapping"]

    for key in REQUIRED_TOP_LEVEL:
        if key not in data:
            errors.append(f"{path.name}: missing required section '{key}'")

    entity_names = set()
    for entity in data.get("entities", []) or []:
        name = entity.get("name")
        if not name:
            errors.append(f"{path.name}: an entity is missing 'name'")
            continue
        entity_names.add(name)
        if not entity.get("source_table"):
            errors.append(f"{path.name}: entity '{name}' missing 'source_table'")
        if not entity.get("columns"):
            errors.append(f"{path.name}: entity '{name}' has no columns")

    for rel in data.get("relationships", []) or []:
        for side in ("from", "to"):
            ref = rel.get(side, "")
            entity = ref.split(".")[0] if "." in ref else ref
            if entity not in entity_names:
                errors.append(
                    f"{path.name}: relationship '{side}: {ref}' references "
                    f"unknown entity '{entity}'"
                )

    for measure in data.get("measures", []) or []:
        entity = measure.get("entity")
        if entity and entity not in entity_names:
            errors.append(
                f"{path.name}: measure '{measure.get('name')}' references "
                f"unknown entity '{entity}'"
            )
        if not measure.get("expression"):
            errors.append(
                f"{path.name}: measure '{measure.get('name')}' missing 'expression'"
            )

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
