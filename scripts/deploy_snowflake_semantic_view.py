"""
deploy_snowflake_semantic_view.py

Step 10: takes the OSSIE YAML (or the Snowflake-flavoured YAML your Step 7
conversion produces, if that ends up being a distinct format from the
central OSSIE YAML) and creates/updates the semantic view in Snowflake using
Snowflake's native "create semantic view from YAML" system function.

NOTE: confirm the exact system function name and argument signature against
Snowflake's current docs / the OI converter's output before relying on this
in anything beyond the prototype — SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML is
used below as a placeholder based on the naming your team has been using.
Run with --verify-only first; that mirrors the structural check Snowflake
already does before anything is actually created.

Usage:
    python deploy_snowflake_semantic_view.py --yaml-dir ossie/
    python deploy_snowflake_semantic_view.py --yaml-dir ossie/ --verify-only

Required environment variables (set as GitHub Actions secrets):
    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
    SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE
"""
import argparse
import os
import sys
from pathlib import Path

import snowflake.connector


def get_connection():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        role=os.environ.get("SNOWFLAKE_ROLE"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
    )


def deploy_yaml(conn, yaml_path: Path, verify_only: bool) -> None:
    yaml_text = yaml_path.read_text()
    escaped_yaml = yaml_text.replace("'", "''")

    if verify_only:
        sql = f"SELECT SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{escaped_yaml}', VERIFY_ONLY => TRUE);"
    else:
        sql = f"SELECT SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{escaped_yaml}');"

    cur = conn.cursor()
    try:
        cur.execute(sql)
        result = cur.fetchone()
        mode = "VERIFIED" if verify_only else "DEPLOYED"
        print(f"[{mode}] {yaml_path.name}: {result}")
    finally:
        cur.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yaml-dir", required=True)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Run Snowflake's structural check without creating the view",
    )
    args = parser.parse_args()

    yaml_dir = Path(args.yaml_dir)
    yaml_files = sorted(yaml_dir.glob("*.yaml")) + sorted(yaml_dir.glob("*.yml"))
    if not yaml_files:
        print(f"No YAML files found in {yaml_dir}")
        sys.exit(1)

    conn = get_connection()
    try:
        for yf in yaml_files:
            deploy_yaml(conn, yf, args.verify_only)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
