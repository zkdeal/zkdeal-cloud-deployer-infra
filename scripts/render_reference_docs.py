#!/usr/bin/env python3
"""Generate deterministic API/ABI references from owner-project artifacts."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import (
    DeploymentError,
    ROOT,
    atomic_write,
    atomic_write_json,
    load_json,
    resolve_artifacts,
    require_container,
    sha256_file,
)


METHOD_RE = re.compile(r"\bapp\.(get|post|put|patch|delete)\b")
PATH_RE = re.compile(r"\(\s*([`'\"])(/[^`'\"]+)\1", re.S)


def source_by_id() -> tuple[Path, dict[str, dict[str, Any]]]:
    umbrella, artifacts = resolve_artifacts()
    return umbrella, {item["id"]: item for item in artifacts}


def route_inventory(root: Path, umbrella: Path) -> tuple[list[dict], list[dict]]:
    routes: list[dict] = []
    sources: list[dict] = []
    for path in sorted(root.rglob("*.ts")):
        text = path.read_text(encoding="utf-8")
        found = 0
        methods = list(METHOD_RE.finditer(text))
        for index, method in enumerate(methods):
            end = methods[index + 1].start() if index + 1 < len(methods) else min(len(text), method.end() + 2_000)
            found_path = PATH_RE.search(text, method.end(), end)
            if not found_path:
                continue
            route = found_path.group(2).replace("\n", "").strip()
            if "${" in route:
                route = route.replace("${", "{").replace("}", "}")
            route = re.sub(r":([A-Za-z][A-Za-z0-9_]*)", r"{\1}", route)
            routes.append({
                "method": method.group(1).upper(),
                "path": route,
                "source": path.name,
            })
            found += 1
        if found:
            sources.append({"path": path.relative_to(umbrella).as_posix(), "sha256": sha256_file(path), "routes": found})
    routes.sort(key=lambda item: (item["path"], item["method"], item["source"]))
    return routes, sources


def typescript_string_map(text: str, name: str) -> dict[str, str]:
    match = re.search(
        rf"export const {re.escape(name)} = Object\.freeze\(\{{(.*?)\}}\s+as const\)",
        text,
        re.S,
    )
    if not match:
        raise DeploymentError(f"owner mapping {name} is absent")
    values = dict(re.findall(r"^\s*([A-Za-z0-9_]+):\s*'([^']+)',", match.group(1), re.M))
    if not values:
        raise DeploymentError(f"owner mapping {name} is empty")
    return dict(sorted(values.items()))


def event_mapping(routes_root: Path, umbrella: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = routes_root / "hosted-indexer.ts"
    text = source.read_text(encoding="utf-8")
    mapping = {
        "schemaVersion": 1,
        "source": "owner-hosted-indexer",
        "roomManager": {
            "events": typescript_string_map(text, "ROOM_MANAGER_EVENT_KINDS"),
            "calls": typescript_string_map(text, "ROOM_MANAGER_CALL_KINDS"),
        },
        "roomPool": {
            "events": typescript_string_map(text, "ROOM_POOL_EVENT_KINDS"),
            "calls": typescript_string_map(text, "ROOM_POOL_CALL_KINDS"),
        },
    }
    required_lifecycle = {
        "roomPool.events": (mapping["roomPool"]["events"], {"NodeDrainStarted", "NodeRetired"}),
        "roomPool.calls": (mapping["roomPool"]["calls"], {"beginNodeDrain", "retireNode"}),
    }
    lifecycle_gaps: list[str] = []
    for label, (catalog, required) in required_lifecycle.items():
        missing = sorted(required - set(catalog))
        if missing:
            lifecycle_gaps.append(f"{label}={missing}")
    if lifecycle_gaps:
        raise DeploymentError(
            "owner hosted indexer omits lifecycle entries required by the current ABI: "
            + "; ".join(lifecycle_gaps)
        )
    source_row = {"path": source.relative_to(umbrella).as_posix(), "sha256": sha256_file(source)}
    mapping["ownerSource"] = source_row
    return mapping, source_row


def render_event_mapping(mapping: dict[str, Any]) -> str:
    lines = [
        "# Owner event and calldata to indexer mapping",
        "",
        "Generated from `web2-api/server/src/hosted-indexer.ts`. Categories are owner-defined, and this deployment project does not decode or project protocol state itself.",
        "",
    ]
    for source_name, title in (("roomManager", "RoomManager"), ("roomPool", "RoomPool")):
        lines.extend([f"## {title} events", "", "| Event | Projection category |", "|---|---|"])
        lines.extend(f"| `{name}` | `{kind}` |" for name, kind in mapping[source_name]["events"].items())
        lines.extend(["", f"## {title} state-changing calls", "", "| Call | Projection category |", "|---|---|"])
        lines.extend(f"| `{name}` | `{kind}` |" for name, kind in mapping[source_name]["calls"].items())
        lines.append("")
    source = mapping["ownerSource"]
    lines.extend(["## Source hash", "", f"- `{source['path']}` — `sha256:{source['sha256']}`", ""])
    return "\n".join(lines)


def abi_type(item: dict) -> str:
    value = str(item.get("type", "unknown"))
    if value.startswith("tuple"):
        suffix = value[5:]
        components = ",".join(abi_type(component) for component in item.get("components", []))
        return f"({components}){suffix}"
    return value


def abi_signature(item: dict) -> str:
    inputs = ",".join(abi_type(value) for value in item.get("inputs", []))
    return f"{item.get('name', '<anonymous>')}({inputs})"


def abi_named_parameter(item: dict) -> dict[str, Any]:
    """Preserve ABI field names and tuple shape without inventing semantics."""
    row: dict[str, Any] = {
        "name": str(item.get("name", "")),
        "type": abi_type(item),
    }
    internal_type = item.get("internalType")
    if isinstance(internal_type, str) and internal_type:
        row["internalType"] = internal_type
    if str(item.get("type", "")).startswith("tuple"):
        row["components"] = [abi_named_parameter(value) for value in item.get("components", [])]
    if "indexed" in item:
        row["indexed"] = bool(item["indexed"])
    return row


def artifact_metadata(artifact: dict[str, Any]) -> dict[str, Any]:
    value = artifact.get("metadata", {})
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def natspec_for(metadata: dict[str, Any], section: str, signature: str) -> dict[str, str]:
    output = metadata.get("output", {}) if isinstance(metadata, dict) else {}
    result: dict[str, str] = {}
    for source_name in ("userdoc", "devdoc"):
        source = output.get(source_name, {}) if isinstance(output, dict) else {}
        entries = source.get(section, {}) if isinstance(source, dict) else {}
        value = entries.get(signature, {}) if isinstance(entries, dict) else {}
        if not isinstance(value, dict):
            continue
        for field in ("notice", "details"):
            text = value.get(field)
            if isinstance(text, str) and text.strip():
                result[f"{source_name}.{field}"] = " ".join(text.split())
    return result


def collect_structs(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    structs: dict[str, list[dict[str, Any]]] = {}

    def visit(item: dict[str, Any]) -> None:
        internal = str(item.get("internalType", ""))
        components = item.get("components", [])
        if str(item.get("type", "")).startswith("tuple") and isinstance(components, list):
            if internal.startswith("struct "):
                name = re.sub(r"\[[0-9]*\]$", "", internal.removeprefix("struct "))
                structs[name] = [abi_named_parameter(value) for value in components]
            for value in components:
                if isinstance(value, dict):
                    visit(value)

    for item in items:
        for value in item.get("inputs", []) + item.get("outputs", []):
            if isinstance(value, dict):
                visit(value)
    return dict(sorted(structs.items()))


def find_contract_artifact(out_root: Path, name: str) -> Path:
    candidates = sorted(out_root.glob(f"**/{name}.json"))
    candidates = [path for path in candidates if "build-info" not in path.parts]
    if not candidates:
        raise DeploymentError(f"contract artifact not found for {name} under {out_root}")
    exact_parent = [path for path in candidates if path.parent.name == f"{name}.sol"]
    return exact_parent[0] if exact_parent else candidates[0]


def abi_inventory(out_root: Path, names: list[str], umbrella: Path) -> tuple[list[dict], list[dict]]:
    contracts: list[dict] = []
    sources: list[dict] = []
    for name in names:
        path = find_contract_artifact(out_root, name)
        artifact = load_json(path)
        abi = artifact.get("abi", [])
        metadata = artifact_metadata(artifact)
        identifiers = artifact.get("methodIdentifiers", {})

        def entries(kind: str, doc_section: str) -> list[dict[str, Any]]:
            rows = []
            for item in abi:
                if item.get("type") != kind:
                    continue
                signature = abi_signature(item)
                row: dict[str, Any] = {
                    "name": item.get("name", "<anonymous>"),
                    "signature": signature,
                    "inputs": [abi_named_parameter(value) for value in item.get("inputs", [])],
                    "natspec": natspec_for(metadata, doc_section, signature),
                }
                if kind == "function":
                    selector = identifiers.get(signature)
                    row.update({
                        "selector": f"0x{selector}" if isinstance(selector, str) else None,
                        "stateMutability": item.get("stateMutability"),
                        "outputs": [abi_named_parameter(value) for value in item.get("outputs", [])],
                    })
                elif kind == "event":
                    row["anonymous"] = bool(item.get("anonymous", False))
                rows.append(row)
            return sorted(rows, key=lambda value: value["signature"])

        functions = entries("function", "methods")
        events = entries("event", "events")
        errors = entries("error", "errors")
        contracts.append({
            "contract": name,
            "functions": functions,
            "events": events,
            "errors": errors,
            "structs": collect_structs([item for item in abi if isinstance(item, dict)]),
        })
        sources.append({"path": path.relative_to(umbrella).as_posix(), "sha256": sha256_file(path)})
    return contracts, sources


def render_api(
    routes: list[dict],
    sources: list[dict],
    owner_openapi: dict[str, Any],
    owner_openapi_source: dict[str, Any],
) -> tuple[str, dict]:
    rpc_schema = load_json(ROOT / "docs/schemas/hosting-json-rpc.schema.json")

    def rpc_component_name(name: str) -> str:
        return "JsonRpc" + name[:1].upper() + name[1:]

    def rewrite_rpc_refs(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    f"#/components/schemas/{rpc_component_name(item.removeprefix('#/$defs/'))}"
                    if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/")
                    else rewrite_rpc_refs(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [rewrite_rpc_refs(item) for item in value]
        return value

    rpc_components = {
        rpc_component_name(name): rewrite_rpc_refs(schema)
        for name, schema in rpc_schema["$defs"].items()
    }
    rpc_components["JsonRpcEnvelope"] = {
        "oneOf": [
            {"$ref": "#/components/schemas/JsonRpcRequest"},
            {"$ref": "#/components/schemas/JsonRpcSuccessResponse"},
            {"$ref": "#/components/schemas/JsonRpcErrorResponse"},
        ]
    }
    hosted_schemas: dict[str, Any] = {
        "Error": {
            "type": "object", "additionalProperties": False, "required": ["error"],
            "properties": {"error": {"type": "string"}, "supportedSchemaVersions": {"type": "array", "items": {"type": "integer"}}},
        },
        "TenantUpsert": {
            "type": "object", "additionalProperties": False, "required": ["tenantId"],
            "properties": {
                "tenantId": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,62}$"},
                "displayName": {"type": "string", "maxLength": 200}, "tier": {"type": "string", "maxLength": 64},
                "limits": {"type": "object"},
            },
        },
        "TenantCreated": {"type": "object", "required": ["tenantId"], "properties": {"tenantId": {"type": "string"}}},
        "PrincipalProvision": {
            "type": "object", "additionalProperties": False, "required": ["kind", "roles"],
            "properties": {
                "kind": {"enum": ["api-key", "node", "service"]},
                "roles": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": [
                    "hosting-admin", "tenant-admin", "room-operator", "job-submit", "job-read", "deadline-set",
                    "usage-read", "withdrawal-read", "withdrawal-claim", "capacity-manage", "indexer-write", "prove-node",
                ]}},
                "limits": {"type": "object"},
            },
        },
        "PrincipalIssued": {
            "type": "object", "additionalProperties": False, "required": ["principalId", "token"],
            "properties": {"principalId": {"type": "string", "pattern": "^(key|node|svc)_[0-9a-f]{20}$"}, "token": {"type": "string", "writeOnly": True}},
        },
        "CapacityIntent": {
            "type": "object", "additionalProperties": False,
            "required": ["allocationId", "roomId", "desiredState", "idempotencyKey"],
            "properties": {
                "allocationId": {"type": "string", "minLength": 1}, "roomId": {"type": "string", "pattern": "^(0|[1-9][0-9]*)$"},
                "desiredState": {"enum": ["RESERVED", "ACTIVE", "RENEW", "HANDOFF", "RELEASED"]},
                "providerNodeId": {"type": ["string", "null"]}, "deadlineAt": {"type": ["string", "null"], "format": "date-time"},
                "idempotencyKey": {"type": "string", "minLength": 1}, "previousIdempotencyKey": {"type": ["string", "null"]},
                "metadata": {"type": "object"},
            },
        },
        "CapacityAccepted": {"type": "object", "required": ["accepted", "allocationId"], "properties": {"accepted": {"const": True}, "allocationId": {"type": "string"}}},
        "CanonicalBlock": {
            "type": "object", "additionalProperties": False, "required": ["chainId", "number", "hash", "parentHash"],
            "properties": {
                "chainId": {"type": "integer", "minimum": 1}, "number": {"type": "string", "pattern": "^(0|[1-9][0-9]*)$"},
                "hash": {"type": "string", "pattern": "^0x[0-9a-fA-F]{64}$"}, "parentHash": {"type": "string", "pattern": "^0x[0-9a-fA-F]{64}$"},
                "observedAt": {"type": "string", "format": "date-time"},
            },
        },
        "CanonicalBlocks": {"type": "object", "additionalProperties": False, "required": ["blocks"], "properties": {"blocks": {"type": "array", "minItems": 1, "items": {"$ref": "#/components/schemas/CanonicalBlock"}}}},
        "RoomObservation": {
            "type": "object", "additionalProperties": False, "required": ["chainId", "headBlock", "headHash", "document"],
            "properties": {
                "chainId": {"type": "integer", "minimum": 1}, "schemaVersion": {"type": "integer", "minimum": 1, "maximum": 1000},
                "headBlock": {"type": "string", "pattern": "^(0|[1-9][0-9]*)$"}, "headHash": {"type": "string", "pattern": "^0x[0-9a-fA-F]{64}$"},
                "document": {"type": "object"},
            },
        },
        "RoomObservationResult": {"type": "object", "required": ["stored", "reconciled"], "properties": {"stored": {"type": "boolean"}, "reconciled": {"type": "boolean"}, "errors": {"type": "array", "items": {"type": "string"}}}},
        "AdmissionLease": {"type": "object", "additionalProperties": False, "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1024}, "leaseMs": {"type": "integer", "minimum": 1, "maximum": 3600000}}},
        "AdmissionLeaseResult": {"type": "object", "required": ["entries"], "properties": {"entries": {"type": "array", "items": {"type": "object"}}}},
        "AdmissionAck": {"type": "object", "additionalProperties": False, "required": ["admissionIds"], "properties": {"admissionIds": {"type": "array", "items": {"type": "string", "pattern": "^(0|[1-9][0-9]*)$"}}}},
        "AdmissionAckResult": {"type": "object", "required": ["acknowledged"], "properties": {"acknowledged": {"type": "integer", "minimum": 0}}},
    }
    hosted_schemas.update(rpc_components)
    request_schema = {
        ("POST", "/hosting/v1/tenants"): "TenantUpsert",
        ("POST", "/hosting/v1/tenants/{tenantId}/principals"): "PrincipalProvision",
        ("POST", "/hosting/v1/capacity/intents"): "CapacityIntent",
        ("POST", "/hosting/v1/indexer/blocks"): "CanonicalBlocks",
        ("PUT", "/hosting/v1/indexer/rooms/{roomId}"): "RoomObservation",
        ("POST", "/hosting/v1/admissions/{roomId}/lease"): "AdmissionLease",
        ("POST", "/hosting/v1/admissions/{roomId}/ack"): "AdmissionAck",
        ("POST", "/hosting/v1/json-rpc"): "JsonRpcRequest",
    }
    response_schema = {
        ("POST", "/hosting/v1/tenants"): "TenantCreated",
        ("POST", "/hosting/v1/tenants/{tenantId}/principals"): "PrincipalIssued",
        ("POST", "/hosting/v1/capacity/intents"): "CapacityAccepted",
        ("PUT", "/hosting/v1/indexer/rooms/{roomId}"): "RoomObservationResult",
        ("POST", "/hosting/v1/admissions/{roomId}/lease"): "AdmissionLeaseResult",
        ("POST", "/hosting/v1/admissions/{roomId}/ack"): "AdmissionAckResult",
        ("POST", "/hosting/v1/json-rpc"): "JsonRpcEnvelope",
    }
    public = {
        ("GET", "/hosting/v1/capabilities"), ("GET", "/hosting/v1/health"),
        ("GET", "/hosting/v1/ready"), ("GET", "/hosting/v1/openapi.json"),
    }
    lines = [
        "# Owner API reference",
        "",
        "Generated from the owner's static OpenAPI artifact plus TypeScript route registration inventory. Exact owner operations and schemas are authoritative and are never replaced by route-scan summaries; source hash changes make this reference stale and fail its container gate.",
        "",
        "## Authentication, schema negotiation and errors",
        "",
        "Hosted protected routes use `Authorization: Bearer <principal>`. Send `Accept-Schema-Version: 1`; every hosted response returns `Content-Schema-Version`. Error bodies use `{\"error\":\"...\"}` with 400 malformed, 401 invalid credential, 403 role/tenant denial, 404 scoped resource missing, 406 unsupported schema, 409 canonical/freshness conflict, 429 bounded rate limit, and 503 unfenced/unconfigured authority.",
        "",
        "Capacity intents are idempotent by `(tenant, allocationId, idempotencyKey)`. Usage pages resume from decimal `usageId`. SSE resumes from `Last-Event-ID`. Principal creation is intentionally non-idempotent; retain the returned secret and rotate/revoke that principal. Indexer mutations are fenced and canonical/hash-bound.",
        "",
        "## Request and response schemas",
        "",
        "The generated OpenAPI document contains concrete schemas for tenant provisioning, principal issuance, capacity intents, canonical blocks, room observations, admission lease/ack, and every owner-published hosted JSON-RPC method. `docs/schemas/hosting-json-rpc.schema.json` is the standalone JSON Schema for JSON-RPC replay tests.",
        "",
        "## Route inventory",
        "",
        "| Method | Path | Owner source |",
        "|---|---|---|",
    ]
    for route in routes:
        lines.append(f"| `{route['method']}` | `{route['path']}` | `{route['source']}` |")
    lines.extend(["", "## Source hashes", ""])
    for source in sources:
        lines.append(f"- `{source['path']}` — `sha256:{source['sha256']}`")
    paths: dict[str, dict] = {}
    for route in routes:
        path = route["path"]
        if path.startswith("/") and "${" not in path:
            operation = {
                "summary": f"Owner route from {route['source']}",
                "parameters": [{"name": "Accept-Schema-Version", "in": "header", "required": False, "schema": {"type": "integer", "enum": [1]}}] if path.startswith("/hosting/") else [],
                "responses": {
                    "200": {"description": "Owner response", **({"content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{response_schema[(route['method'], path)]}"}}}} if (route["method"], path) in response_schema else {})},
                    "400": {"description": "Malformed request", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "406": {"description": "Unsupported schema version", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "429": {"description": "Principal rate limit", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "503": {"description": "Authority unavailable or fenced", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                } if path.startswith("/hosting/") else {"default": {"description": "Owner response"}},
                "x-owner-source": route["source"],
            }
            key = (route["method"], path)
            if key in request_schema:
                operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{request_schema[key]}"}}}}
            if path.startswith("/hosting/"):
                operation["security"] = [] if key in public else [{"bearerAuth": []}]
            paths.setdefault(path, {})[route["method"].lower()] = operation
    if owner_openapi.get("openapi") != "3.1.0":
        raise DeploymentError("owner hosting OpenAPI must declare version 3.1.0")
    if not isinstance(owner_openapi.get("paths"), dict) or not owner_openapi["paths"]:
        raise DeploymentError("owner hosting OpenAPI has no paths")
    if not isinstance(owner_openapi.get("components"), dict):
        raise DeploymentError("owner hosting OpenAPI has no components object")

    # The static owner document is authoritative. The route scan is a coverage
    # inventory only: it may add a missing operation but may never replace or
    # shallow-merge an exact owner request, response, error, auth, or
    # idempotency contract.
    openapi = copy.deepcopy(owner_openapi)
    owner_components = openapi.setdefault("components", {})
    owner_schemas = owner_components.setdefault("schemas", {})
    if not isinstance(owner_schemas, dict):
        raise DeploymentError("owner hosting OpenAPI components.schemas is not an object")
    for name, schema in hosted_schemas.items():
        owner_schemas.setdefault(name, schema)
    security_schemes = owner_components.setdefault("securitySchemes", {})
    if not isinstance(security_schemes, dict):
        raise DeploymentError("owner hosting OpenAPI components.securitySchemes is not an object")
    security_schemes.setdefault("bearerAuth", {"type": "http", "scheme": "bearer"})
    owner_paths = openapi["paths"]
    added_operations: list[str] = []
    for path, methods in paths.items():
        existing = owner_paths.setdefault(path, {})
        if not isinstance(existing, dict):
            raise DeploymentError(f"owner hosting OpenAPI path item is not an object: {path}")
        for method, operation in methods.items():
            if method not in existing:
                existing[method] = operation
                added_operations.append(f"{method.upper()} {path}")
    openapi["x-deployment-route-inventory"] = {
        "ownerOpenapiSource": owner_openapi_source,
        "routeSources": sources,
        "fallbackOperationsAdded": sorted(added_operations),
        "authoritativeOperationsPreserved": True,
    }
    return "\n".join(lines) + "\n", openapi


def render_abi(contracts: list[dict], sources: list[dict]) -> str:
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").strip() or "—"

    def parameters(values: list[dict[str, Any]]) -> str:
        rendered = []
        for value in values:
            name = value.get("name") or "<unnamed>"
            indexed = " indexed" if value.get("indexed") else ""
            rendered.append(f"{value.get('type', 'unknown')} {name}{indexed}")
        return "; ".join(rendered) or "—"

    def natspec(value: dict[str, str]) -> str:
        return " ".join(f"{name}: {text}" for name, text in sorted(value.items())) or "—"

    lines = [
        "# Contract ABI reference",
        "",
        "Generated from Foundry artifacts owned by `web3-protocol`. This reference preserves canonical selectors, named ABI fields, tuple/struct layouts and available owner NatSpec; deployment consumes the original artifacts and never copies contract bytecode here.",
        "",
    ]
    for contract in contracts:
        lines.extend([
            f"## {contract['contract']}", "", "### Functions", "",
            "| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |",
            "|---|---|---|---|---|---|",
        ])
        if contract["functions"]:
            for value in contract["functions"]:
                lines.append(
                    f"| `{cell(value['signature'])}` | `{cell(value.get('selector'))}` | "
                    f"`{cell(value.get('stateMutability'))}` | `{cell(parameters(value['inputs']))}` | "
                    f"`{cell(parameters(value['outputs']))}` | {cell(natspec(value['natspec']))} |"
                )
        else:
            lines.append("| None | — | — | — | — | — |")
        lines.extend([
            "", "### Events", "",
            "| Signature | Named/indexed fields | Anonymous | Owner NatSpec |",
            "|---|---|---|---|",
        ])
        if contract["events"]:
            for value in contract["events"]:
                lines.append(
                    f"| `{cell(value['signature'])}` | `{cell(parameters(value['inputs']))}` | "
                    f"`{str(value.get('anonymous', False)).lower()}` | {cell(natspec(value['natspec']))} |"
                )
        else:
            lines.append("| None | — | — | — |")
        lines.extend([
            "", "### Custom errors", "",
            "| Signature | Named fields | Owner NatSpec |", "|---|---|---|",
        ])
        if contract["errors"]:
            for value in contract["errors"]:
                lines.append(
                    f"| `{cell(value['signature'])}` | `{cell(parameters(value['inputs']))}` | "
                    f"{cell(natspec(value['natspec']))} |"
                )
        else:
            lines.append("| None | — | — |")
        lines.extend(["", "### Struct and tuple layouts", ""])
        if contract["structs"]:
            for name, fields in contract["structs"].items():
                lines.extend([f"#### `{name}`", "", "| Field | ABI type | Internal type |", "|---|---|---|"])
                for field in fields:
                    lines.append(
                        f"| `{cell(field.get('name') or '<unnamed>')}` | `{cell(field.get('type'))}` | "
                        f"`{cell(field.get('internalType'))}` |"
                    )
                lines.append("")
        else:
            lines.append("- No named tuple/struct fields appear in this artifact ABI.")
        lines.append("")
    lines.extend(["## Source hashes", ""])
    for source in sources:
        lines.append(f"- `{source['path']}` — `sha256:{source['sha256']}`")
    return "\n".join(lines) + "\n"


def build() -> dict[str, bytes]:
    umbrella, artifacts = source_by_id()
    routes_root = (umbrella / artifacts["coordinator-routes"]["path"]).resolve()
    abi_root = (umbrella / artifacts["contract-abis"]["path"]).resolve()
    contract_names = load_json(ROOT / "config" / "reference-contracts.json")["contracts"]
    routes, route_sources = route_inventory(routes_root, umbrella)
    contracts, abi_sources = abi_inventory(abi_root, contract_names, umbrella)
    mappings, mapping_source = event_mapping(routes_root, umbrella)
    capability_path = (umbrella / artifacts["hosted-service-capabilities"]["path"]).resolve()
    capabilities = load_json(capability_path)
    capability_source = {"path": capability_path.relative_to(umbrella).as_posix(), "sha256": sha256_file(capability_path)}
    headless_capability_path = (umbrella / artifacts["headless-room-node-capabilities"]["path"]).resolve()
    headless_capabilities = load_json(headless_capability_path)
    headless_capability_source = {
        "path": headless_capability_path.relative_to(umbrella).as_posix(),
        "sha256": sha256_file(headless_capability_path),
    }
    capacity_provider_path = (umbrella / artifacts["capacity-provider-openapi"]["path"]).resolve()
    capacity_provider = load_json(capacity_provider_path)
    capacity_provider_source = {
        "path": capacity_provider_path.relative_to(umbrella).as_posix(),
        "sha256": sha256_file(capacity_provider_path),
    }
    runtime_openapi_path = (umbrella / artifacts["hosting-runtime-openapi"]["path"]).resolve()
    runtime_openapi = load_json(runtime_openapi_path)
    runtime_openapi_source = {
        "path": runtime_openapi_path.relative_to(umbrella).as_posix(),
        "sha256": sha256_file(runtime_openapi_path),
    }
    api_markdown, openapi = render_api(routes, route_sources, runtime_openapi, runtime_openapi_source)
    abi_markdown = render_abi(contracts, abi_sources)
    manifest = {
        "schemaVersion": 1,
        "generator": "scripts/render_reference_docs.py",
        "apiRoutes": len(routes),
        "contracts": len(contracts),
        "sources": route_sources + abi_sources + [
            mapping_source, capability_source, headless_capability_source,
            capacity_provider_source, runtime_openapi_source,
        ],
    }
    return {
        "api.md": api_markdown.encode(),
        "abi.md": abi_markdown.encode(),
        "openapi.reference.json": (json.dumps(openapi, indent=2, sort_keys=True) + "\n").encode(),
        "hosted-service-capabilities.reference.json": (json.dumps(capabilities, indent=2, sort_keys=True) + "\n").encode(),
        "headless-room-node-capabilities.reference.json": (json.dumps(headless_capabilities, indent=2, sort_keys=True) + "\n").encode(),
        "capacity-provider-v1.openapi.reference.json": (json.dumps(capacity_provider, indent=2, sort_keys=True) + "\n").encode(),
        "event-to-indexer.json": (json.dumps(mappings, indent=2, sort_keys=True) + "\n").encode(),
        "event-to-indexer.md": render_event_mapping(mappings).encode(),
        "manifest.json": (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-dir", default="docs/generated")
    args = parser.parse_args()
    try:
        require_container()
        rendered = build()
        output = (ROOT / args.output_dir).resolve()
        if args.check:
            stale = [name for name, content in rendered.items() if not (output / name).is_file() or (output / name).read_bytes() != content]
            if stale:
                print("stale generated references: " + ", ".join(stale), file=sys.stderr)
                return 1
            print(f"generated references are current ({len(rendered)} files)")
            return 0
        for name, content in rendered.items():
            atomic_write(output / name, content)
        print(json.dumps({"output": str(output), "files": sorted(rendered)}, indent=2))
        return 0
    except (DeploymentError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
