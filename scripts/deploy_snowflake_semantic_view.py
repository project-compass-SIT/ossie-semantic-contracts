"""
deploy_snowflake_semantic_view.py

Step 10: creates/updates a Snowflake semantic view directly from OSSIE YAML,
using Snowflake's native Ossie-YAML stored procedure:

    SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML(
        '<fully_qualified_schema_name>',
        '<ossie_yaml_specification>'
    )

Docs: https://docs.snowflake.com/sql-reference/stored-procedures/system_create_semantic_view_from_ossie_yaml

Two things worth knowing before you rely on this:

1. It's a STORED PROCEDURE (CALL), not a function - that's why this script
   uses CALL rather than SELECT.
2. As of the current docs, this procedure does not document a verify-only /
   dry-run mode (unlike its native-Snowflake-YAML sibling,
   SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML, which takes a boolean verify_only
   argument). There is no --verify-only flag here for that reason - test
   against a scratch/dev schema first instead of assuming a dry run exists.

Your sample YAML wraps everything under a `semantic_model:` list (the same
"document wrapper" shape SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW returns),
but Snowflake's documented syntax for the CREATE procedure shows `name`,
`version`, `datasets`, `relationships`, `metrics` etc. flat at the top level,
with no `semantic_model` key recognized. This script unwraps the former into
the latter automatically, since the flat shape is what's actually documented
for this procedure's input.

Usage:
    python deploy_snowflake_semantic_view.py --yaml-dir ossie/ --schema SIT.COMPASS

Required environment variables (set as GitHub Actions secrets):
    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
    SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE
"""
import argparse
import os
import sys
from pathlib import Path

import snowflake.connector
import yaml


def get_connection():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        role=os.environ.get("SNOWFLAKE_ROLE"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
    )


def to_flat_ossie_document(yaml_path: Path) -> str:
    """
    Accepts either shape and returns the flat shape
    (name/version/datasets/relationships/metrics at top level) that
    Snowflake's docs show for SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML.
    """
    raw = yaml.safe_load(yaml_path.read_text())

    sm = raw.get("semantic_model")
    if sm is None:
        # Already flat - pass through as-is.
        return yaml_path.read_text()

    model = sm[0] if isinstance(sm, list) else sm
    flat = dict(model)
    flat.setdefault("version", raw.get("version", "0.1.1"))
    return yaml.dump(flat, sort_keys=False)


def deploy_yaml(conn, yaml_path: Path, target_schema: str) -> None:
    yaml_text = to_flat_ossie_document(yaml_path)

    if "$$" in yaml_text:
        raise ValueError(
            f"{yaml_path.name}: contains '$$', which breaks the dollar-quoted "
            f"string this script uses to pass the YAML to Snowflake. Rename "
            f"that sequence out of the file or adjust the quoting below."
        )

    sql = (
        f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML(\n"
        f"  '{target_schema}',\n"
        f"  $${yaml_text}$$\n"
        f");"
    )

    cur = conn.cursor()
    try:
        cur.execute(sql)
        result = cur.fetchone()
        print(f"[DEPLOYED] {yaml_path.name}: {result}")
    finally:
        cur.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yaml-dir", required=True)
    parser.add_argument(
        "--schema",
        required=True,
        help="Fully qualified target schema, e.g. SIT.COMPASS "
             "(can also be set via SNOWFLAKE_TARGET_SCHEMA env var)",
        default=os.environ.get("SNOWFLAKE_TARGET_SCHEMA"),
    )
    args = parser.parse_args()

    if not args.schema:
        print("Missing --schema (or SNOWFLAKE_TARGET_SCHEMA env var), "
              "e.g. --schema SIT.COMPASS")
        sys.exit(2)

    yaml_dir = Path(args.yaml_dir)
    yaml_files = sorted(yaml_dir.glob("*.yaml")) + sorted(yaml_dir.glob("*.yml"))
    if not yaml_files:
        print(f"No YAML files found in {yaml_dir}")
        sys.exit(1)

    conn = get_connection()
    try:
        for yf in yaml_files:
            deploy_yaml(conn, yf, args.schema)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
