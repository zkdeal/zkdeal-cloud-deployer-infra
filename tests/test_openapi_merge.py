from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError  # noqa: E402
from render_reference_docs import render_api  # noqa: E402


class AuthoritativeOpenApiMergeTests(unittest.TestCase):
    def owner_document(self) -> dict:
        return {
            "openapi": "3.1.0",
            "info": {"title": "Owner hosting API", "version": "19"},
            "paths": {
                "/hosting/v1/tenants": {
                    "post": {
                        "operationId": "ownerCreateTenant",
                        "summary": "Exact owner operation",
                        "parameters": [{"name": "Idempotency-Key", "in": "header", "required": True}],
                        "requestBody": {
                            "required": True,
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OwnerTenant"}}},
                        },
                        "responses": {
                            "201": {"description": "Created"},
                            "409": {"description": "Idempotency conflict"},
                        },
                        "security": [{"ownerBearer": []}],
                    }
                }
            },
            "components": {
                "securitySchemes": {"ownerBearer": {"type": "http", "scheme": "bearer"}},
                "schemas": {
                    "Error": {"type": "object", "required": ["ownerErrorCode"]},
                    "OwnerTenant": {"type": "object", "required": ["tenantId", "billingAccount"]},
                },
            },
        }

    def test_owner_operations_schemas_auth_and_errors_are_never_diluted(self):
        owner = self.owner_document()
        original = copy.deepcopy(owner)
        routes = [
            {"method": "POST", "path": "/hosting/v1/tenants", "source": "hosting-routes.ts"},
            {"method": "GET", "path": "/hosting/v1/health", "source": "hosting-routes.ts"},
        ]
        source = {"path": "web2-api/server/capabilities/hosting-v1.openapi.json", "sha256": "1" * 64}
        markdown, merged = render_api(routes, [], owner, source)

        self.assertEqual(owner, original, "reference generation must not mutate the owner artifact")
        self.assertEqual(
            merged["paths"]["/hosting/v1/tenants"]["post"],
            original["paths"]["/hosting/v1/tenants"]["post"],
        )
        self.assertEqual(merged["components"]["schemas"]["Error"], original["components"]["schemas"]["Error"])
        self.assertEqual(merged["components"]["schemas"]["OwnerTenant"], original["components"]["schemas"]["OwnerTenant"])
        self.assertIn("get", merged["paths"]["/hosting/v1/health"])
        self.assertEqual(
            merged["x-deployment-route-inventory"]["fallbackOperationsAdded"],
            ["GET /hosting/v1/health"],
        )
        self.assertTrue(merged["x-deployment-route-inventory"]["authoritativeOperationsPreserved"])
        self.assertIn("never replaced", markdown)

    def test_invalid_or_skeletal_owner_openapi_fails_closed(self):
        source = {"path": "owner.json", "sha256": "2" * 64}
        invalid = (
            {},
            {"openapi": "3.0.3", "paths": {"/health": {}}, "components": {}},
            {"openapi": "3.1.0", "paths": {}, "components": {}},
            {"openapi": "3.1.0", "paths": {"/health": {}}, "components": []},
        )
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(DeploymentError):
                render_api([], [], document, source)

    def test_required_hosting_facets_are_in_the_contract_reference_set(self):
        config = json.loads((ROOT / "config/reference-contracts.json").read_text(encoding="utf-8"))
        required = {"RoomManagerHostingFacet", "RoomManagerChallengeFacet", "RoomPoolHostingFacet"}
        self.assertFalse(required - set(config["contracts"]))

    def test_owner_static_openapi_is_a_required_artifact(self):
        config = json.loads((ROOT / "config/artifacts.json").read_text(encoding="utf-8"))
        artifact = next(item for item in config["artifacts"] if item["id"] == "hosting-runtime-openapi")
        self.assertTrue(artifact["required"])
        self.assertEqual(artifact["path"], "web2-api/server/capabilities/hosting-v1.openapi.json")
        self.assertEqual(artifact["kind"], "openapi")


if __name__ == "__main__":
    unittest.main()
