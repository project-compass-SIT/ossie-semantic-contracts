import argparse
import base64
import copy
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "https://api.fabric.microsoft.com/v1"
BACKUP = Path("fabric-definition-backup.json")
PLAN = Path("fabric-update-plan.json")


def fail(message):
    raise RuntimeError(message)


def access_token():
    return subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com", "--query", "accessToken", "-o", "tsv"],
        text=True,
    ).strip()


def request(method, url, token, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            return resp.status, resp.headers, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        fail(f"Fabric HTTP {exc.code}: {detail[:2000]}")


def complete(status, headers, result, token, want_result):
    if status == 200:
        return result
    if status != 202:
        fail(f"Unexpected Fabric status {status}")
    operation = headers.get("x-ms-operation-id")
    if not operation:
        fail("Missing operation ID")
    url = f"{BASE}/operations/{operation}"
    deadline = time.monotonic() + 600
    delay = int(headers.get("Retry-After", "10"))
    while time.monotonic() < deadline:
        time.sleep(max(delay, 1))
        _, response_headers, state = request("GET", url, token)
        outcome = state.get("status") if state else None
        if outcome == "Succeeded":
            if want_result:
                _, _, result = request("GET", url + "/result", token)
                return result
            return None
        if outcome in ("Failed", "Cancelled"):
            fail(f"Fabric operation {outcome}: {state}")
        delay = int(response_headers.get("Retry-After", "10"))
    fail("Fabric operation timed out")


def get_definition(workspace, model_id, token):
    url = f"{BASE}/workspaces/{workspace}/semanticModels/{model_id}/getDefinition?format=TMSL"
    result = complete(*request("POST", url, token), token, True)
    if not isinstance(result, dict) or "definition" not in result:
        fail("No semantic model definition returned")
    return result


def unpack(definition):
    parts = definition["definition"]["parts"]
    paths = [part["path"] for part in parts]
    if paths.count("model.bim") != 1 or paths.count("definition.pbism") != 1:
        fail("Expected one model.bim and one definition.pbism")
    if any(path.startswith("definition/") for path in paths):
        fail("Expected TMSL, received TMDL parts")
    part = next(part for part in parts if part["path"] == "model.bim")
    if part["payloadType"] != "InlineBase64":
        fail("Unsupported BIM payload type")
    return json.loads(base64.b64decode(part["payload"], validate=True))


def expression(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "\n".join(value).strip()
    fail("Measure expression must be a string or list of strings")


def valid_expression(measure):
    text = expression(measure.get("expression"))
    if not text or text.upper() in ("BLANK()", "= BLANK()"):
        fail(f"Measure {measure['name']!r} has empty or placeholder DAX")
    return text


def index_tables(bim):
    result = {}
    for table in bim["model"].get("tables", []):
        name = table["name"]
        if name in result:
            fail(f"Duplicate table {name!r}")
        result[name] = table
    return result


def index_measures(bim):
    result = {}
    for table in bim["model"].get("tables", []):
        for measure in table.get("measures", []):
            name = measure["name"]
            if name in result:
                fail(f"Ambiguous measure name {name!r}; assign unique names for automation")
            result[name] = (table, measure)
    return result


def table_columns(table):
    return {column["name"] for column in table.get("columns", [])}


def build(candidate, live, home):
    target = copy.deepcopy(live)
    generated_tables = index_tables(candidate)
    original_tables = index_tables(live)
    target_tables = index_tables(target)
    new_tables = sorted(set(generated_tables) - set(original_tables))
    if new_tables:
        fail("New data tables require explicit source/partition configuration; not deploying: " + ", ".join(new_tables))
    for name, table in generated_tables.items():
        missing = table_columns(table) - table_columns(original_tables[name])
        if missing:
            fail(f"Data table {name!r} has new columns not in the live model: {sorted(missing)}")
    if home not in target_tables:
        fail(f"Create and publish the {home!r} measures table in Fabric first")
    candidate_measures = index_measures(candidate)
    current_measures = index_measures(target)
    if not candidate_measures:
        fail("Candidate BIM contains no measures")
    changes = []
    for name, (_, generated) in sorted(candidate_measures.items()):
        desired = valid_expression(generated)
        if name in current_measures:
            _, existing = current_measures[name]
            if expression(existing["expression"]) != desired:
                existing["expression"] = copy.deepcopy(generated["expression"])
                changes.append({"name": name, "action": "UPDATE", "expression": desired})
        else:
            measure = {
                "name": name,
                "expression": copy.deepcopy(generated["expression"]),
                "lineageTag": str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"fabric:{os.environ['FABRIC_SEMANTIC_MODEL_ID']}:ossie-measure:{name}",
                )),
            }
            target_tables[home].setdefault("measures", []).append(measure)
            changes.append({"name": name, "action": "ADD", "expression": desired})
    return target, changes


def encode_definition(original, merged):
    result = copy.deepcopy(original["definition"])
    payload = base64.b64encode(json.dumps(merged, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")
    for part in result["parts"]:
        if part["path"] == "model.bim":
            part["payload"] = payload
    return result


def assert_plan(backup, plan, home):
    before = unpack(backup)
    after = unpack({"definition": plan["definition"]})
    expected, changes = build(plan["candidate"], before, home)
    if expected != after:
        fail("Prepared BIM changes exceed supported measure changes")
    original_parts = {part["path"]: part for part in backup["definition"]["parts"]}
    new_parts = {part["path"]: part for part in plan["definition"]["parts"]}
    if original_parts.keys() != new_parts.keys():
        fail("Definition parts were added or removed")
    if any(original_parts[key] != new_parts[key] for key in original_parts if key != "model.bim"):
        fail("Non-BIM definition part changed")
    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("prepare", "apply"), required=True)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    workspace = os.environ["FABRIC_WORKSPACE_ID"]
    model_id = os.environ["FABRIC_SEMANTIC_MODEL_ID"]
    home = os.environ.get("FABRIC_MEASURES_TABLE", "OSSIE_MEASURES")
    token = access_token()

    if args.stage == "prepare":
        if not args.candidate:
            fail("--candidate is required for prepare")
        candidate = json.loads(args.candidate.read_text(encoding="utf-8-sig"))
        before = get_definition(workspace, model_id, token)
        live = unpack(before)
        merged, changes = build(candidate, live, home)
        if not changes:
            print("No measure differences; no update necessary")
            return
        plan = {"workspace": workspace, "model": model_id, "home": home, "candidate": candidate, "definition": encode_definition(before, merged)}
        BACKUP.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
        PLAN.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        print("Planned: " + ", ".join(f"{item['action']} {item['name']}" for item in changes))
        print("Backup ready; update not yet sent")
        return

    if not BACKUP.is_file() or not PLAN.is_file():
        fail("No prepared update; nothing to apply")
    backup = json.loads(BACKUP.read_text(encoding="utf-8"))
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    if plan["workspace"] != workspace or plan["model"] != model_id or plan["home"] != home:
        fail("Target or home table differs from prepared plan")
    changes = assert_plan(backup, plan, home)
    if not changes:
        fail("Prepared plan contains no changes")
    latest = get_definition(workspace, model_id, token)
    if latest["definition"]["parts"] != backup["definition"]["parts"]:
        fail("Live model changed since backup; refusing update")
    url = f"{BASE}/workspaces/{workspace}/semanticModels/{model_id}/updateDefinition"
    complete(*request("POST", url, token, {"definition": plan["definition"]}), token, False)
    checked = get_definition(workspace, model_id, token)
    found = index_measures(unpack(checked))
    for item in changes:
        if item["name"] not in found or expression(found[item["name"]][1]["expression"]) != item["expression"]:
            fail(f"Read-back mismatch for {item['name']!r}")
    print("Verified " + ", ".join(item["name"] for item in changes))


if __name__ == "__main__":
    main()
