#!/usr/bin/env python3
"""Compare static/live owner OpenAPI and replay schema-validated examples."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource

from common import DeploymentError, require_container


OPENAPI_RESOURCE = "urn:zkdeal:hosting-openapi"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DeploymentError(f"expected JSON object: {path}")
    return value


def request_json(
    base_url: str,
    method: str,
    path: str,
    headers: dict[str, str],
    body: Any,
    timeout: float,
) -> tuple[int, dict[str, str], Any]:
    payload = None if body is None else canonical_bytes(body)
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=payload,
        method=method.upper(),
        headers=headers,
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    raw = response.read()
    normalized_headers = {name.lower(): value for name, value in response.headers.items()}
    if not raw:
        value: Any = None
    else:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeploymentError(f"{method} {path} returned non-JSON body") from exc
    return int(response.status), normalized_headers, value


def openapi_operation(document: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    paths = document.get("paths")
    operation = paths.get(path, {}).get(method.lower()) if isinstance(paths, dict) else None
    if not isinstance(operation, dict):
        raise DeploymentError(f"OpenAPI has no exact operation for {method.upper()} {path}")
    return operation


def registry_for(document: dict[str, Any]) -> Registry:
    resource_document = copy.deepcopy(document)
    resource_document.setdefault("$schema", JSON_SCHEMA_DIALECT)
    return Registry().with_resource(OPENAPI_RESOURCE, Resource.from_contents(resource_document))


def absolute_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: OPENAPI_RESOURCE + item if key == "$ref" and isinstance(item, str) and item.startswith("#/") else absolute_refs(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [absolute_refs(item) for item in value]
    return value


def validate_instance(instance: Any, schema: Any, document: dict[str, Any], label: str) -> None:
    if not isinstance(schema, dict):
        raise DeploymentError(f"{label} has no JSON schema object")
    try:
        jsonschema.Draft202012Validator(absolute_refs(schema), registry=registry_for(document)).validate(instance)
    except jsonschema.ValidationError as exc:
        raise DeploymentError(f"{label} schema validation failed: {exc.message}") from exc


def json_pointer(value: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise DeploymentError(f"stable response pointer must start with '/': {pointer}")
    current = value
    for raw in pointer.removeprefix("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise DeploymentError(f"response lacks stable pointer {pointer}")
    return current


def response_contract(operation: dict[str, Any], status: int) -> tuple[dict[str, Any], Any | None]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        raise DeploymentError("OpenAPI operation has no responses object")
    response = responses.get(str(status)) or responses.get(f"{status // 100}XX") or responses.get("default")
    if not isinstance(response, dict):
        raise DeploymentError(f"OpenAPI operation has no response contract for HTTP {status}")
    content = response.get("content", {})
    media = content.get("application/json", {}) if isinstance(content, dict) else {}
    schema = media.get("schema") if isinstance(media, dict) else None
    return response, schema


def replay_example(
    document: dict[str, Any],
    base_url: str,
    example: dict[str, Any],
    token: str | None,
    timeout: float,
) -> dict[str, Any]:
    name = str(example.get("name", "")).strip()
    method = str(example.get("method", "")).upper()
    path = str(example.get("path", ""))
    if not name or method.lower() not in HTTP_METHODS or not path.startswith("/"):
        raise DeploymentError("every example requires name, HTTP method, and absolute path")
    operation = openapi_operation(document, method, path)
    body = example.get("body")
    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        media = request_body.get("content", {}).get("application/json", {})
        schema = media.get("schema") if isinstance(media, dict) else None
        if request_body.get("required") and body is None:
            raise DeploymentError(f"{name} omits its required request body")
        if body is not None and schema is not None:
            validate_instance(body, schema, document, f"{name} request")
    elif body is not None:
        raise DeploymentError(f"{name} supplies a body absent from the owner OpenAPI operation")

    headers = {str(key).lower(): str(value) for key, value in example.get("headers", {}).items()}
    if "authorization" in headers:
        raise DeploymentError(f"{name} embeds an authorization secret in the example file")
    auth = example.get("auth", "none")
    if auth == "bearer":
        if not token:
            raise DeploymentError(f"{name} requires the bearer token environment variable")
        headers["authorization"] = f"Bearer {token}"
    elif auth != "none":
        raise DeploymentError(f"{name} has unsupported auth mode {auth}")
    if body is not None:
        headers.setdefault("content-type", "application/json")
    headers.setdefault("accept-schema-version", "1")

    mutation = bool(example.get("mutation", False))
    idempotency_key = example.get("idempotencyKey")
    pointers = example.get("stableResponsePointers", [])
    if mutation:
        if method in {"GET", "HEAD", "OPTIONS"}:
            raise DeploymentError(f"{name} marks a read method as a mutation")
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 200:
            raise DeploymentError(f"{name} mutation requires an 8-200 byte idempotencyKey")
        if not isinstance(pointers, list) or not pointers or not all(isinstance(item, str) for item in pointers):
            raise DeploymentError(f"{name} mutation requires stableResponsePointers for exact replay assertions")
        headers["idempotency-key"] = idempotency_key
    elif idempotency_key is not None:
        raise DeploymentError(f"{name} supplies an idempotency key without mutation=true")

    expected_statuses = example.get("expectedStatus")
    if isinstance(expected_statuses, int):
        expected_statuses = [expected_statuses]
    if not isinstance(expected_statuses, list) or not expected_statuses or not all(isinstance(item, int) for item in expected_statuses):
        raise DeploymentError(f"{name} requires expectedStatus as an integer or integer list")

    def once() -> tuple[int, Any]:
        status, response_headers, value = request_json(base_url, method, path, headers, body, timeout)
        if status not in expected_statuses:
            raise DeploymentError(f"{name} returned HTTP {status}, expected {expected_statuses}")
        if example.get("requireSchemaHeader", path.startswith("/hosting/")):
            if response_headers.get("content-schema-version") != "1":
                raise DeploymentError(f"{name} omitted Content-Schema-Version: 1")
        _contract, schema = response_contract(operation, status)
        if value is not None:
            validate_instance(value, schema, document, f"{name} response {status}")
        return status, value

    status, first = once()
    replayed = False
    if mutation:
        second_status, second = once()
        if second_status != status:
            raise DeploymentError(f"{name} idempotent replay changed HTTP status")
        for pointer in pointers:
            if json_pointer(first, pointer) != json_pointer(second, pointer):
                raise DeploymentError(f"{name} idempotent replay changed {pointer}")
        replayed = True
    return {"name": name, "method": method, "path": path, "status": status, "mutationReplay": replayed}


def run(static_path: Path, examples_path: Path, base_url: str, token_env: str, timeout: float) -> dict[str, Any]:
    static = load_object(static_path)
    examples_document = load_object(examples_path)
    if static.get("openapi") != "3.1.0" or not isinstance(static.get("paths"), dict):
        raise DeploymentError("static owner OpenAPI is not a complete 3.1 document")
    status, _headers, live = request_json(base_url, "GET", "/hosting/v1/openapi.json", {"accept": "application/json"}, None, timeout)
    if status != 200 or not isinstance(live, dict):
        raise DeploymentError(f"live owner OpenAPI returned HTTP {status}")
    if canonical_bytes(static) != canonical_bytes(live):
        raise DeploymentError(f"static/live owner OpenAPI mismatch: static={sha256(static)} live={sha256(live)}")
    if examples_document.get("schemaVersion") != 1 or not isinstance(examples_document.get("examples"), list):
        raise DeploymentError("example manifest must have schemaVersion 1 and examples[]")
    token = os.environ.get(token_env)
    results = [replay_example(static, base_url, example, token, timeout) for example in examples_document["examples"]]
    if not results:
        raise DeploymentError("example manifest is empty")
    return {
        "passed": True,
        "openapiSha256": sha256(static),
        "staticLiveEqual": True,
        "examples": results,
        "mutationReplays": sum(1 for item in results if item["mutationReplay"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static", required=True)
    parser.add_argument("--examples", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-env", default="ZKDEAL_OPENAPI_REPLAY_TOKEN")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    try:
        require_container()
        result = run(Path(args.static).resolve(), Path(args.examples).resolve(), args.base_url, args.token_env, args.timeout)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (DeploymentError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
