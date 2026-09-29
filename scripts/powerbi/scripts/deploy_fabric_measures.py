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


def token():
    return subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com", "--query", "accessToken", "-o", "tsv"],
        text=True,
    ).strip()


def request(method, url, access_token, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            raw = response.read()
            return response.status, response.headers, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Fabric HTTP {exc.code}: {detail[:2000]}") from exc


def complete(status, headers, response, access_token, result_required):
    if status == 200:
        return response
    if status != 202:
        raise RuntimeError(f"Unexpected Fabric status: {status}")
    operation_id = headers.get("x-ms-operation-id")
    if not operation_id:
        raise RuntimeError("202 response lacks x-ms-operation-id")
    state_url = f"{API}/operations/{operation_id}"
    deadline = time.monotonic() + 600
    delay = int(headers.get("Retry-After", "10"))
    while time.monotonic() < deadline:
        time.sleep(max(1, delay))
        _, state_headers, state = request("GET", state_url, access_token)
        status_name = state.get("status") if state else None
        if status_name == "Succeeded":
            if result_required:
                _, _, value = request("GET", state_url + "/result", access_token)
                return value
            return None
        if status_name in ("Failed", "Cancelled"):
            raise RuntimeError(f"Fabric operation {status_name}: {state}")
        delay = int(state_headers.get("Retry-After", "10"))
    raise TimeoutError(f"Fabric operation {operation_id} timed out")


def definition(workspace, model_id, access_token):
    url = f"{API}/workspaces/{workspace}/semanticModels/{model_id}/getDefinition?format=TMSL"
    return complete(*request("POST", url, access_token), access_token, True)


def bim_part(result):
    definition_object = result["definition"]
    if definition_object.get("format") not in (None, "TMSL"):
        raise ValueError("Fabric response was not TMSL")
    parts = definition_object["parts"]
    matches = [p for p in parts if p["path"] == "model.bim"]
    if len(matches) != 1 or "definition.pbism" not in {p["path"] for p in parts}:
        raise ValueError("Expected exactly one model.bim and a definition.pbism")
    if any(p["path"].startswith("definition/") for p in parts):
        raise ValueError("TMDL parts found in TMSL definition")
    part = matches[0]
    if part["payloadType"] != "InlineBase64":
        raise ValueError("Unsupported BIM payload type")
    return json.loads(base64.b64decode(part["payload"], validate=True)), part


def unique(items, name, kind):
    matches = [item for item in items if item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {kind} named {name!r}; found {len(matches)}")
    return matches[0]


def normalized(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return "\n".join(value).strip()
    raise ValueError("Measure expression must be a string or list of strings")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--table", required=True)
    parser.add_argument("--measure", required=True)
    parser.add_argument("--stage", choices=("prepare", "apply"), required=True)
    args = parser.parse_args()
    workspace = os.environ["FABRIC_WORKSPACE_ID"]
    model_id = os.environ["FABRIC_SEMANTIC_MODEL_ID"]
    access_token = token()
    backup_path = Path("fabric-definition-backup.json")
    plan_path = Path("fabric-update-plan.json")

    if args.stage == "prepare":
        current = definition(workspace, model_id, access_token)
        live, _ = bim_part(current)
        candidate = json.loads(args.candidate.read_text(encoding="utf-8-sig"))
        live_table = unique(live["model"]["tables"], args.table, "live table")
        candidate_table = unique(candidate["model"]["tables"], args.table, "candidate table")
        old = unique(live_table.get("measures", []), args.measure, "live measure")
        new = unique(candidate_table.get("measures", []), args.measure, "candidate measure")
        expected = normalized(new["expression"])
        if not expected:
            raise ValueError("Generated measure expression is empty")
        if normalized(old["expression"]) == expected:
            print("No measure expression change; no update is needed")
            return

        merged = copy.deepcopy(live)
        dest = unique(
            unique(merged["model"]["tables"], args.table, "merged table").get("measures", []),
            args.measure,
            "merged measure",
        )
        dest["expression"] = copy.deepcopy(new["expression"])
        updated = copy.deepcopy(current["definition"])
        bim_json = json.dumps(merged, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        unique(updated["parts"], "model.bim", "definition part") if False else None
        for part in updated["parts"]:
            if part["path"] == "model.bim":
                part["payload"] = base64.b64encode(bim_json).decode("ascii")

        backup_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        plan_path.write_text(
            json.dumps({"workspace": workspace, "model": model_id, "table": args.table,
                        "measure": args.measure, "definition": updated}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Prepared expression-only update for {args.table!r}/{args.measure!r}")
        print(f"Backed up {len(current['definition']['parts'])} definition parts; awaiting artifact upload")
        return

    if not backup_path.is_file() or not plan_path.is_file():
        raise RuntimeError("Prepare stage did not produce backup and plan")
    before = json.loads(backup_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan["workspace"] != workspace or plan["model"] != model_id:
        raise RuntimeError("Prepared target differs from current target")
    if plan["table"] != args.table or plan["measure"] != args.measure:
        raise RuntimeError("Prepared measure differs from approved measure")
    current = definition(workspace, model_id, access_token)
    if current["definition"]["parts"] != before["definition"]["parts"]:
        raise RuntimeError("Live definition changed since backup; refusing to update")
    proposed = {p["path"]: p for p in plan["definition"]["parts"]}
    existing = {p["path"]: p for p in before["definition"]["parts"]}
    if proposed.keys() != existing.keys():
        raise RuntimeError("Definition parts changed in the prepared plan")
    if any(proposed[k] != existing[k] for k in existing if k != "model.bim"):
        raise RuntimeError("Non-BIM definition parts changed in the prepared plan")
    live_bim, _ = bim_part(before)
    planned_bim, _ = bim_part({"definition": plan["definition"]})
    old_measure = unique(unique(live_bim["model"]["tables"], args.table, "live table")["measures"], args.measure, "live measure")
    proposed_measure = unique(unique(planned_bim["model"]["tables"], args.table, "planned table")["measures"], args.measure, "planned measure")
    control = copy.deepcopy(live_bim)
    unique(unique(control["model"]["tables"], args.table, "control table")["measures"], args.measure, "control measure")["expression"] = copy.deepcopy(proposed_measure["expression"])
    if control != planned_bim or normalized(old_measure["expression"]) == normalized(proposed_measure["expression"]):
        raise RuntimeError("Plan modifies something besides the requested measure expression")

    url = f"{API}/workspaces/{workspace}/semanticModels/{model_id}/updateDefinition"
    complete(*request("POST", url, access_token, {"definition": plan["definition"]}), access_token, False)
    verified = definition(workspace, model_id, access_token)
    actual_bim, _ = bim_part(verified)
    actual = unique(unique(actual_bim["model"]["tables"], args.table, "verified table")["measures"], args.measure, "verified measure")
    if normalized(actual["expression"]) != normalized(proposed_measure["expression"]):
        raise RuntimeError("Read-back measure expression mismatch")
    print(f"Update verified for {args.table!r}/{args.measure!r}")


if __name__ == "__main__":
    main()
