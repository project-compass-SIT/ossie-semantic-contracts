import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import snowflake.connector


AUDIENCE = "snowflakecomputing.com"


def get_github_oidc_token() -> str:
    base_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    request_token = os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]

    separator = "&" if "?" in base_url else "?"
    url = (
        f"{base_url}{separator}"
        f"audience={urllib.parse.quote(AUDIENCE, safe='')}"
    )

    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {request_token}"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)["value"]


def run_procedure(cursor, schema_name: str, yaml_text: str, verify: bool) -> str:
    cursor.execute(
        "CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(%s, %s, %s)",
        (schema_name, yaml_text, verify),
    )

    result = cursor.fetchone()
    if result is None or result[0] is None:
        raise RuntimeError("Snowflake returned no procedure status.")

    return str(result[0])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yaml", required=True, type=Path)
    parser.add_argument("--target-schema", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    if not args.yaml.is_file():
        parser.error(f"YAML file not found: {args.yaml}")

    # Snowflake requires a database-qualified schema, e.g. SIT.COMPASS.
    if len(args.target_schema.split(".")) != 2:
        parser.error("--target-schema must be DATABASE.SCHEMA")

    yaml_text = args.yaml.read_text(encoding="utf-8")
    if not yaml_text.strip():
        parser.error("YAML file is empty")

    connection = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=os.environ["SNOWFLAKE_ROLE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        authenticator="WORKLOAD_IDENTITY",
        workload_identity_provider="OIDC",
        token=get_github_oidc_token(),
    )

    try:
        cursor = connection.cursor()
        try:
            identity = cursor.execute(
                "SELECT CURRENT_USER(), CURRENT_ROLE()"
            ).fetchone()
            print(f"Connected as {identity[0]} with role {identity[1]}")

            verification = run_procedure(
                cursor, args.target_schema, yaml_text, verify=True
            )
            print(f"Verification: {verification}")

            if args.verify_only:
                print("Verification-only mode: no semantic view deployed.")
                return

            deployment = run_procedure(
                cursor, args.target_schema, yaml_text, verify=False
            )
            print(f"Deployment: {deployment}")
        finally:
            cursor.close()
    finally:
        connection.close()


if __name__ == "__main__":
    main()