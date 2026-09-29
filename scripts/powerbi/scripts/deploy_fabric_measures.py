import argparse
import base64
import copy
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.fabric.microsoft.com/v1"


def fabric_token():
    return subprocess.check_output(
        [
            "az", "account", "get-access-token",
            "--resource", "https://api.fabric.microsoft.com",
            "--query", "accessToken",
            "-o", "tsv",
        ],
        text=True,
    ).strip()


def call(method, url, token, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read()
            return response.status, response.headers, (
                json.loads(body) if body else None
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Fabric HTTP {exc.code}: {detail[:2000]}") from exc


def finish_operation(status, headers, result, token, expect_result):
    if status != 202:
        return result

    operation_id = headers.get("x-ms-operation-id")
    if not operation_id:
        raise RuntimeError("Fabric returned 202 without x-ms-operation-id")

    url = f"{API}/operations/{operation_id}"
    deadline = time.monotonic() + 600
    delay = int(headers.get("Retry-After", "10"))

    while time.monotonic() < deadline:
        time.sleep(max(delay, 1))
        _, headers, state = call("GET", url, token)
        outcome = state.get("status") if state else None

        if outcome == "Succeeded":
            if expect_result:
                _, _, result = call("GET", url + "/result", token)
                return result
            return None

        if outcome in ("Failed", "Cancelled"):
            raise RuntimeError(f"Fabric operation {outcome}: {state}")

        delay = int(headers.get("Retry-After", "10"))

    raise TimeoutError(f"Fabric operation {operation_id} did not finish")


def get_definition(workspace, model_id, token):
    url = (
        f"{API}/workspaces/{workspace}/semanticModels/{model_id}"
        "/getDefinition?format=TMSL"
    )
    status, headers, result = call("POST", url, token)
    return finish_operation(status, headers, result, token, expect_result=True)


def decode_bim(definition):
    parts = definition["definition"]["parts"]
    matches = [part for part in parts if part["path"] == "model.bim"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one model.bim part")
    if "definition.pbism" not in {part["path"] for part in parts}:
        raise ValueError("definition.pbism part is missing")
    if any(part["path"].startswith("definition/") for part in parts):
        raise ValueError("Received TMDL parts, not a TMSL definition")
    part = matches[0]
    if part["payloadType"] != "InlineBase64":
        raise ValueError("Unsupported BIM payload type")
    bim = json.loads(base64.b64decode(part["payload"], validate=True))
    return bim, part


def unique_by_name(items, name, kind):
    matches = [item for item in items if item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {kind} named {name!r}; found {len(matches)}"
        )
    return matches[0]


def normalize_expression(expression):
    if isinstance(expression, str):
        return expression.strip()
    if isinstance(expression, list) and all(
        isinstance(line, str) for line in expression
    ):
        return "\n".join(expression).strip()
    raise ValueError("Measure expression is not a string or list of strings")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--table", required=True)
    parser.add_argument("--measure", required=True, action="append")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    workspace = os.environ["FABRIC_WORKSPACE_ID"]
    model_id = os.environ["FABRIC_SEMANTIC_MODEL_ID"]
    token = fabric_token()

    current_definition = get_definition(workspace, model_id, token)
    current_bim, _ = decode_bim(current_definition)
    candidate_bim = json.loads(args.candidate.read_text(encoding="utf-8-sig"))

    live_table = unique_by_name(
        current_bim["model"]["tables"], args.table, "live table"
    )
    candidate_table = unique_by_name(
        candidate_bim["model"]["tables"], args.table, "candidate table"
    )

    merged_bim = copy.deepcopy(current_bim)
    merged_table = unique_by_name(
        merged_bim["model"]["tables"], args.table, "merged table"
    )

    if len(set(args.measure)) != len(args.measure):
        raise ValueError("The same measure was specified more than once")

    changes = {}
    for name in args.measure:
        original = unique_by_name(
            live_table.get("measures", []), name, "live measure"
        )
        generated = unique_by_name(
            candidate_table.get("measures", []), name, "candidate measure"
        )
        merged = unique_by_name(
            merged_table.get("measures", []), name, "merged measure"
        )

        old_expression = normalize_expression(original["expression"])
        new_expression = normalize_expression(generated["expression"])
        if not new_expression:
            raise ValueError(f"Generated expression is empty: {name}")
        if old_expression != new_expression:
            merged["expression"] = generated["expression"]
            changes[name] = new_expression

    if not changes:
        print("No approved measure expressions changed; nothing to deploy.")
        return

    print(
        f"Table {args.table!r}: changing expressions for "
        f"{', '.join(sorted(changes))}"
    )

    if not args.apply:
        print("DRY RUN ONLY. Rerun with --apply after reviewing the measures.")
        return

    # Check for model drift immediately before sending the replacement.
    latest_definition = get_definition(workspace, model_id, token)
    if latest_definition["definition"]["parts"] != \
            current_definition["definition"]["parts"]:
        raise RuntimeError("Live definition changed during this run; aborting")

    # Save only on the runner. Arrange a protected, durable backup separately.
    backup = Path("fabric-definition-backup.json")
    with backup.open("w", encoding="utf-8") as fh:
        json.dump(current_definition, fh)

    updated_definition = copy.deepcopy(current_definition["definition"])
    updated_definition["parts"] = [
        {
            **part,
            "payload": base64.b64encode(
                json.dumps(
                    merged_bim, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ).decode("ascii"),
        } if part["path"] == "model.bim" else part
        for part in updated_definition["parts"]
    ]

    url = (
        f"{API}/workspaces/{workspace}/semanticModels/{model_id}"
        "/updateDefinition"
    )
    status, headers, result = call(
        "POST", url, token, {"definition": updated_definition}
    )
    finish_operation(status, headers, result, token, expect_result=False)

    verified_definition = get_definition(workspace, model_id, token)
    verified_bim, _ = decode_bim(verified_definition)
    verified_table = unique_by_name(
        verified_bim["model"]["tables"], args.table, "verified table"
    )

    for name, expected in changes.items():
        actual = unique_by_name(
            verified_table.get("measures", []), name, "verified measure"
        )
        if normalize_expression(actual["expression"]) != expected:
            raise RuntimeError(f"Read-back mismatch for measure {name!r}")

    print("Fabric update succeeded; all changed measures verified by read-back.")


if __name__ == "__main__":
    main()