from __future__ import annotations

import json
import sys
import tempfile
import unittest
import hashlib
import yaml
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError  # noqa: E402
import final_candidate as candidate  # noqa: E402


class FinalCandidateTests(unittest.TestCase):
    def test_plan_orders_seal_before_regeneration_and_live_gates(self):
        names = [name for name, _, _ in candidate.PLAN]
        self.assertLess(names.index("owner-source-seal"), names.index("reference-regeneration"))
        self.assertLess(names.index("deployment-unit-policy"), names.index("candidate-source-bundle"))
        self.assertLess(names.index("candidate-source-bundle"), names.index("4090-transfer-verification"))
        self.assertLess(names.index("4090-transfer-verification"), names.index("4090-zkvm-source-closure"))
        self.assertLess(names.index("4090-zkvm-source-closure"), names.index("4090-zkvm-image-staging"))
        self.assertLess(names.index("4090-zkvm-image-staging"), names.index("4090-staged-image-receipt"))
        self.assertLess(names.index("4090-staged-image-receipt"), names.index("4090-two-build-trust-root"))
        self.assertLess(names.index("4090-zkvm-source-closure"), names.index("4090-two-build-trust-root"))
        self.assertLess(names.index("4090-two-build-trust-root"), names.index("4090-generated-trust-root-closure"))
        self.assertLess(names.index("headless-image-build"), names.index("prover-agent-image-build"))
        self.assertLess(names.index("prover-agent-image-build"), names.index("deployment-image-builds"))
        self.assertLess(names.index("deployment-image-builds"), names.index("acceptance-runner-image-build"))
        self.assertLess(names.index("acceptance-runner-image-build"), names.index("failover-runner-image-build"))
        self.assertLess(names.index("failover-runner-image-build"), names.index("soak-runner-image-build"))
        self.assertLess(names.index("soak-runner-image-build"), names.index("candidate-oci-staging"))
        self.assertLess(names.index("candidate-oci-staging"), names.index("release-digest-seal"))
        self.assertLess(names.index("release-digest-seal"), names.index("production-compose-live"))
        self.assertLess(names.index("production-compose-live"), names.index("prover-agent-owner-boundary"))
        self.assertLess(names.index("prover-agent-owner-boundary"), names.index("owner-openapi-live-replay"))
        self.assertLess(names.index("owner-openapi-live-replay"), names.index("postgres-ha-rehearsal"))
        self.assertLess(names.index("openbao-web3signer-boundaries"), names.index("helm-production-render"))
        self.assertLess(names.index("kubernetes-failover-provider"), names.index("candidate-private-topology-verification"))
        self.assertLess(names.index("candidate-private-topology-verification"), names.index("kurtosis-local"))
        self.assertLess(names.index("candidate-private-topology-verification"), names.index("kurtosis-acceptance-matrix"))
        self.assertLess(names.index("kurtosis-acceptance-matrix"), names.index("acceptance-credential-revocation"))
        self.assertLess(names.index("acceptance-credential-revocation"), names.index("release-soak-12h"))
        self.assertLess(names.index("physical-single-calldata-settlement"), names.index("release-soak-12h"))
        self.assertLess(names.index("release-soak-12h"), names.index("4090-source-generated-composite-seal"))
        self.assertLess(names.index("4090-source-generated-composite-seal"), names.index("4090-trust-root-pre-promotion-check"))
        self.assertLess(names.index("4090-trust-root-pre-promotion-check"), names.index("candidate-oci-promotion"))
        self.assertLess(names.index("candidate-oci-promotion"), names.index("physical-evidence-seal"))
        self.assertLess(names.index("physical-evidence-seal"), names.index("yellow-paper-pdf-render"))
        self.assertLess(names.index("investor-deck-overflow-fidelity"), names.index("publication-artifact-seal"))
        self.assertLess(names.index("publication-artifact-seal"), names.index("candidate-evidence-bundle"))
        self.assertLess(names.index("candidate-evidence-bundle"), names.index("evidence-worm-closure"))
        self.assertLess(names.index("evidence-worm-closure"), names.index("4090-trust-root-post-evidence-check"))
        self.assertEqual(names[-1], "4090-trust-root-post-evidence-check")
        self.assertFalse(any("git" in command.lower().split() for _, _, command in candidate.PLAN))
        commands = {name: command for name, _, command in candidate.PLAN}
        self.assertIn("POSTGRES_HA_BASE_IMAGE=<candidate postgres digest>", commands["postgres-ha-rehearsal"])
        self.assertIn("FRONT_DOOR_IMAGE=<candidate frontDoor digest>", commands["edge-controls"])
        self.assertIn("OPENBAO_IMAGE=<candidate openbao digest>", commands["openbao-web3signer-boundaries"])
        self.assertIn("MINIO_CLIENT_IMAGE=<candidate minioClient digest>", commands["database-object-backup-restore"])
        self.assertIn("FAILOVER_PROVIDER_CANDIDATE_IMAGE=<candidate failoverProvider digest>", commands["docker-failover-provider"])
        self.assertIn("POSTGRES_HA_BASE_IMAGE=<candidate postgres digest>", commands["docker-failover-provider"])
        self.assertIn("FAILOVER_PROVIDER_CANDIDATE_IMAGE=<candidate failoverProvider digest>", commands["kubernetes-failover-provider"])
        self.assertIn("POSTGRES_CANDIDATE_IMAGE=<candidate postgres digest>", commands["kubernetes-failover-provider"])
        self.assertIn("trust-root-output", commands["4090-generated-trust-root-closure"])
        self.assertIn("staged-images", commands["4090-staged-image-receipt"])
        self.assertIn("--bootstrap-lock", commands["4090-two-build-trust-root"])
        self.assertNotIn("--update-lock", commands["4090-two-build-trust-root"])
        self.assertIn("staged-zkvm-images.json", commands["4090-generated-trust-root-closure"])
        self.assertIn("evidence-closure", commands["4090-source-generated-composite-seal"])
        self.assertIn("trust-root-check", commands["4090-trust-root-pre-promotion-check"])
        self.assertIn("rebuilding", commands["candidate-oci-promotion"])
        self.assertIn("--input-file", commands["kurtosis-acceptance-matrix"])
        self.assertIn("plan_file", commands["kurtosis-acceptance-matrix"])
        self.assertIn("auth_files", commands["kurtosis-acceptance-matrix"])
        self.assertIn("never token values", commands["kurtosis-acceptance-matrix"])
        self.assertIn("candidate_topology.py check", commands["candidate-private-topology-verification"])
        self.assertIn("distinct fault/backup", commands["candidate-private-topology-verification"])
        self.assertIn("not release evidence", commands["kurtosis-local"])

    def test_release_images_reject_mutable_placeholder_and_unreviewed_reuse(self):
        digest = "1" * 64
        images = {
            name: f"registry.company.tld/zkdeal/{name.lower()}@sha256:{digest}"
            for name in candidate.REQUIRED_IMAGES
        }
        self.assertEqual(set(candidate.validate_release_images({"images": images})), candidate.REQUIRED_IMAGES)
        reused = dict(images)
        reused["agent"] = reused["coordinator"]
        with self.assertRaisesRegex(DeploymentError, "reference reuse"):
            candidate.validate_release_images({"images": reused})
        for bad in (
            "zkdeal-coordinator:latest",
            f"registry.company.tld/zkdeal/coordinator:latest@sha256:{digest}",
            "registry.invalid/zkdeal/coordinator@sha256:" + digest,
            "registry.company.tld/zkdeal/coordinator@sha256:REPLACE",
        ):
            broken = dict(images)
            broken["coordinator"] = bad
            with self.subTest(reference=bad), self.assertRaises(DeploymentError):
                candidate.validate_release_images({"images": broken})

    def test_compose_and_helm_inputs_must_use_candidate_images(self):
        digest = "1" * 64
        images = {
            name: f"registry.company.tld/zkdeal/{name.lower()}@sha256:{digest}"
            for name in candidate.REQUIRED_IMAGES
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = root / "runtime.env"
            env.write_text("\n".join(
                f"{variable}={images[image_name]}"
                for variable, image_name in candidate.COMPOSE_IMAGE_MAP.items()
            ) + "\n", encoding="utf-8")
            candidate.validate_compose_candidate_images(env, images)
            env.write_text(env.read_text(encoding="utf-8").replace(
                images["coordinator"], images["headlessNode"], 1,
            ), encoding="utf-8")
            with self.assertRaisesRegex(DeploymentError, "differs from candidate"):
                candidate.validate_compose_candidate_images(env, images)

            def image(name: str) -> dict[str, str]:
                repository, digest_value = images[name].split("@", 1)
                return {"repository": repository, "tag": "", "digest": digest_value}

            values = root / "values.yaml"
            values.write_text(yaml.safe_dump({
                "components": {
                    "coordinatorActive": {"image": image("coordinator")},
                    "queue": {"enabled": False},
                    "docs": {"image": image("docs")},
                },
            }), encoding="utf-8")
            candidate.validate_helm_candidate_images(values, images)
            broken = yaml.safe_load(values.read_text(encoding="utf-8"))
            broken["components"]["docs"]["image"] = image("headlessNode")
            values.write_text(yaml.safe_dump(broken), encoding="utf-8")
            with self.assertRaisesRegex(DeploymentError, "differs from candidate"):
                candidate.validate_helm_candidate_images(values, images)
            broken["components"]["docs"]["image"] = image("docs")
            broken["components"]["tenantApi"] = {"enabled": True, "image": image("coordinator")}
            values.write_text(yaml.safe_dump(broken), encoding="utf-8")
            with self.assertRaisesRegex(DeploymentError, "unmapped owner components"):
                candidate.validate_helm_candidate_images(values, images)

    def test_acceptance_nested_inputs_are_project_local_hash_bound_and_never_return_values(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "cloud-deployer-infra"
            state = project / ".state/candidates/candidate-inputs"
            auth = state / "auth"
            auth.mkdir(parents=True)
            token = "eph_" + "a" * 32
            token_path = auth / "tenant-a.token"
            token_path.write_bytes(token.encode())
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            plan_path = state / "acceptance-plan.json"
            plan_path.write_text(json.dumps({
                "authTokenSha256": {"tenant_a": token_hash},
            }, sort_keys=True), encoding="utf-8")
            args_path = state / "acceptance-matrix.json"
            args_path.write_text("{}", encoding="utf-8")
            payload = {
                "plan_file": "acceptance-plan.json",
                "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                "auth_files": {"tenant_a": "auth/tenant-a.token"},
            }
            with patch.object(candidate, "ROOT", project):
                checked = candidate.acceptance_nested_input_hashes(args_path, payload)
                self.assertEqual(checked["authFiles"]["tenant_a"]["sha256"], token_hash)
                self.assertFalse(checked["credentialValuesRecorded"])
                self.assertNotIn(token, json.dumps(checked))
                token_path.write_bytes((token + "\n").encode())
                with self.assertRaisesRegex(DeploymentError, "without whitespace"):
                    candidate.acceptance_nested_input_hashes(args_path, payload)

                outside = Path(folder) / "outside.token"
                outside.write_bytes(token.encode())
                payload["auth_files"]["tenant_a"] = str(outside)
                with self.assertRaisesRegex(DeploymentError, "escaped"):
                    candidate.acceptance_nested_input_hashes(args_path, payload)

    def test_owner_seal_cannot_be_self_attested(self):
        value = {
            "ownerBroadSeal": {
                "passed": False,
                "evidenceSha256": "1" * 64,
                "gateContractSha256": candidate.OWNER_GATE_CONTRACT_SHA256,
                "hostedIntegrationAcceptanceToken": "sha256:" + "2" * 64,
                "gates": {name: True for name in candidate.REQUIRED_OWNER_GATES},
            },
            "sourceArtifacts": {},
        }
        with self.assertRaisesRegex(DeploymentError, "not passed"):
            candidate.validate_owner_seal(value)

    def test_owner_seal_hash_without_real_manifest_is_rejected(self):
        value = {
            "candidateId": "candidate-20260821",
            "ownerBroadSeal": {
                "passed": True,
                "evidenceManifestPath": "web2-api/server/evidence/missing.json",
                "evidenceSha256": "1" * 64,
                "gateContractSha256": candidate.OWNER_GATE_CONTRACT_SHA256,
                "hostedIntegrationAcceptanceToken": "sha256:" + "2" * 64,
                "gates": {name: True for name in candidate.REQUIRED_OWNER_GATES},
            },
            "sourceArtifacts": {},
        }
        with tempfile.TemporaryDirectory() as folder, patch.object(
            candidate, "inventory", return_value={"umbrellaRoot": folder, "artifacts": []},
        ), self.assertRaisesRegex(DeploymentError, "absent or not a regular file"):
            candidate.validate_owner_seal(value)

    def test_example_is_intentionally_fail_closed_and_complete(self):
        example = json.loads((ROOT / "config/final-candidate.example.json").read_text(encoding="utf-8"))
        self.assertFalse(example["ownerBroadSeal"]["passed"])
        self.assertIsNone(example["ownerBroadSeal"]["gateContractSha256"])
        self.assertEqual(set(example["ownerBroadSeal"]["gates"]), candidate.REQUIRED_OWNER_GATES)
        self.assertEqual(set(example["images"]), candidate.REQUIRED_IMAGES)
        self.assertTrue(all(value is None for value in example["images"].values()))

    def test_gate_contract_is_hash_bound_and_has_exact_candidate_gate_set(self):
        policy = json.loads((ROOT / "config/owner-release-gates.json").read_text(encoding="utf-8"))
        self.assertEqual(set(policy["gates"]), candidate.REQUIRED_OWNER_GATES)
        self.assertEqual(
            hashlib.sha256((ROOT / "config/owner-release-gates.json").read_bytes()).hexdigest(),
            candidate.OWNER_GATE_CONTRACT_SHA256,
        )
        for required in (
            "hostingServiceTopology", "managedAggregateL1Operations",
            "managedSponsorshipL1Operations", "managedWithdrawalL1Operations",
            "proverAgentTraceJoin", "durableHostedQueueAndHeartbeat",
        ):
            self.assertIn(required, policy["gates"])

    def test_generated_zkvm_trust_root_is_phase_b_and_binds_exact_staged_images(self):
        with tempfile.TemporaryDirectory() as folder:
            umbrella = Path(folder)
            project = umbrella / "cloud-deployer-infra"
            project.mkdir()
            candidate_sha = "1" * 64
            lock_sha = "2" * 64
            program_id = "0x" + "3" * 64
            staged = {
                "orchestrator": "registry.company.tld/zkdeal/orchestrator@sha256:" + "4" * 64,
                "toolchain": "registry.company.tld/zkdeal/toolchain@sha256:" + "5" * 64,
                "runtime": "registry.company.tld/zkdeal/runtime@sha256:" + "6" * 64,
            }

            def write(relative: str, value: dict) -> dict[str, str]:
                path = umbrella / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
                return {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }

            source = write("closure/source.json", {
                "schema": "zkdeal/4090-source-closure/v1",
                "algorithm": "sha256",
                "noRepositoryHistory": True,
                "noSecrets": True,
                "zkvmCandidateManifest": {"sha256": candidate_sha},
            })
            receipt_value = {
                "schema": "zkdeal/4090-staged-zkvm-images/v1",
                "candidateManifestSha256": candidate_sha,
                "promoted": False,
                "images": staged,
            }
            receipt = write("closure/staged.json", receipt_value)
            closure_value = {
                "schema": "zkdeal/4090-generated-trust-root-closure/v1",
                "algorithm": "sha256",
                "buildPreimage": {
                    "verifiedAgainstFilesystem": True,
                    "generatedOutputsExcluded": True,
                    "candidateManifest": {"sha256": candidate_sha},
                },
                "stagedImages": {
                    "receiptSha256": receipt["sha256"], "promoted": False, **staged,
                },
                "generatedTrustRoot": {
                    "sourceManifest": {
                        "sha256": candidate_sha, "byteIdenticalToCandidate": True,
                    },
                    "artifactLock": {
                        "sha256": lock_sha, "format": "zkdeal/zkvm-artifacts-lock/v6",
                    },
                    "programId": program_id,
                    "toolchainImage": staged["toolchain"],
                    "runtimeImage": staged["runtime"],
                    "lockedArtifacts": [
                        {"path": path, "bytes": index + 1, "sha256": digit * 64}
                        for index, (path, digit) in enumerate(zip(
                            sorted(candidate.REQUIRED_ZKVM_LOCKED_ARTIFACTS),
                            ("4", "5", "6", "7", "8", "9", "a"),
                        ))
                    ],
                },
                "orderingContract": {
                    "requiredIndependentCudaBuilds": 2,
                    "trustRootWriter": "zkvm/build.mjs --cuda --check-repro --bootstrap-lock",
                    "stagedImagesAreNotReleasePromotion": True,
                    "finalPromotionRequiresExactStagedDigests": True,
                    "rebuildAfterCompositeSealForbidden": True,
                    "postCompositeSealMutationInvalidatesCandidate": True,
                },
            }
            closure = write("closure/generated.json", closure_value)
            value = {"zkvmGeneratedTrustRoot": {
                "passed": True,
                "sourceClosure": source,
                "stagedImagesReceipt": receipt,
                "generatedClosure": closure,
                "candidateManifestSha256": candidate_sha,
                "artifactLockSha256": lock_sha,
                "programId": program_id,
                "stagedImages": staged,
            }}
            with patch.object(candidate, "ROOT", project):
                result = candidate.validate_generated_trust_root(value, {"prover": staged["runtime"]})
                self.assertEqual(result["stagedImages"], staged)
                self.assertEqual(
                    set(result["lockedArtifacts"]), candidate.REQUIRED_ZKVM_LOCKED_ARTIFACTS,
                )
                broken = json.loads(json.dumps(value))
                broken["zkvmGeneratedTrustRoot"]["stagedImages"]["runtime"] = (
                    "registry.company.tld/zkdeal/runtime@sha256:" + "9" * 64
                )
                with self.assertRaisesRegex(DeploymentError, "candidate prover image|staged-image"):
                    candidate.validate_generated_trust_root(broken, {"prover": staged["runtime"]})

    def test_generated_zkvm_lock_is_not_a_phase_a_owner_source_artifact(self):
        artifacts = json.loads((ROOT / "config/artifacts.json").read_text(encoding="utf-8"))
        trust_root = next(item for item in artifacts["artifacts"] if item["id"] == "zkvm-trust-root")
        self.assertFalse(trust_root["required"])
        self.assertEqual(trust_root["phase"], "post-two-cuda-build")
        example = json.loads((ROOT / "config/final-candidate.example.json").read_text(encoding="utf-8"))
        self.assertNotIn("zkvm-trust-root", example["sourceArtifacts"])
        self.assertIn("zkvmGeneratedTrustRoot", example)
        candidate.validate_phase_a_unminted(example)
        example["zkvmGeneratedTrustRoot"]["artifactLockSha256"] = "1" * 64
        with self.assertRaisesRegex(DeploymentError, "prefilled artifactLockSha256"):
            candidate.validate_phase_a_unminted(example)

    def test_physical_and_publication_manifests_bind_candidate_and_exact_files(self):
        with tempfile.TemporaryDirectory() as folder:
            umbrella = Path(folder)
            project = umbrella / "cloud-deployer-infra"
            state = project / ".state/candidates/candidate-physical"
            state.mkdir(parents=True)
            physical_root = umbrella / "physical"
            physical_root.mkdir()

            def write_record(name: str, value: dict) -> dict[str, str]:
                path = physical_root / f"{name}.json"
                path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
                return {
                    "path": path.relative_to(umbrella).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }

            staged_images = {
                "orchestrator": "registry.company.tld/zkdeal/orchestrator@sha256:" + "1" * 64,
                "toolchain": "registry.company.tld/zkdeal/toolchain@sha256:" + "2" * 64,
                "runtime": "registry.company.tld/zkdeal/prover@sha256:" + "3" * 64,
            }
            source = write_record("source", {"schema": "zkdeal/4090-source-closure/v1"})
            staged = write_record("staged", {
                "schema": "zkdeal/4090-staged-zkvm-images/v1",
                "promoted": False,
                "images": staged_images,
            })
            generated = write_record("generated", {
                "schema": "zkdeal/4090-generated-trust-root-closure/v1",
                "algorithm": "sha256",
            })
            candidate_path = state / "candidate.json"
            token = "sha256:" + "4" * 64
            candidate_value = {
                "candidateId": "candidate-physical",
                "ownerBroadSeal": {"hostedIntegrationAcceptanceToken": token},
                "zkvmGeneratedTrustRoot": {
                    "passed": True,
                    "sourceClosure": source,
                    "stagedImagesReceipt": staged,
                    "generatedClosure": generated,
                    "candidateManifestSha256": "5" * 64,
                    "artifactLockSha256": "6" * 64,
                    "programId": "0x" + "7" * 64,
                    "stagedImages": staged_images,
                },
                "images": {"prover": staged_images["runtime"]},
            }
            candidate_path.write_text(json.dumps(candidate_value, sort_keys=True) + "\n", encoding="utf-8")

            records = {}
            for name in candidate.REQUIRED_PHYSICAL_RECORDS:
                records[name] = write_record(name, {"record": name})
            records["sourceClosure"] = source
            records["stagedZkvmImages"] = staged
            records["generatedTrustRootClosure"] = generated

            generated_path = umbrella / generated["path"]
            composite_value = {
                "schema": "zkdeal/4090-evidence-closure/v2",
                "algorithm": "sha256",
                "source": {
                    "closureSha256": source["sha256"],
                    "generatedTrustRootClosureSha256": generated["sha256"],
                    "zkvmManifestSha256": "5" * 64,
                },
                "physicalAcceptance": {
                    "ownerAcceptanceToken": token,
                    "ownerDurableCapabilitiesSha256": records["ownerDurableCapabilities"]["sha256"],
                    "settlementScenarioSha256": "8" * 64,
                    "deploymentAddressesSha256": records["freshDeploymentAddresses"]["sha256"],
                    "soakVerificationSha256": records["soakVerification"]["sha256"],
                },
                "artifactLockSha256": "6" * 64,
                "orchestratorImage": staged_images["orchestrator"],
                "toolchainImage": staged_images["toolchain"],
                "runtimeImage": staged_images["runtime"],
                "programId": "0x" + "7" * 64,
                "files": [{
                    "path": generated_path.name,
                    "size": generated_path.stat().st_size,
                    "sha256": generated["sha256"],
                }],
            }
            composite = write_record("evidence-closure", composite_value)
            records["sourceGeneratedCompositeSeal"] = composite
            records["trustRootPrePromotionCheck"] = write_record("trust-root-check", {
                "verified": True,
                "schema": "zkdeal/4090-generated-trust-root-closure/v1",
                "sha256": generated["sha256"],
            })
            daemon_id = "sha256:" + "9" * 64
            promoted_ref = "registry.company.tld/zkdeal/releases/candidate-physical/prover@sha256:" + "3" * 64
            promotion_payload = {
                "schema": "zkdeal/oci-promotion/v1",
                "candidateId": "candidate-physical",
                "release": "candidate-physical",
                "artifact": "prover",
                "imageKey": "prover",
                "candidateDescriptorSha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
                "sourceGeneratedCompositeSealSha256": composite["sha256"],
                "sourcePublicationManifestSha256": "a" * 64,
                "sourceImmutableReference": staged_images["runtime"],
                "promotedImmutableReference": promoted_ref,
                "digest": "sha256:" + "3" * 64,
                "sourceDaemonImageId": daemon_id,
                "promotedDaemonImageId": daemon_id,
                "sizeBytes": 123,
                "exactDigestPreserved": True,
                "sameDaemonImageId": True,
                "rebuilt": False,
                "mutableReferenceRecorded": False,
                "transportReferenceRemovedLocally": True,
                "promotionOccurredAfterCompositeSeal": True,
            }
            promotion_auth = {
                "algorithm": "hmac-sha256",
                "keyId": "sha256:" + "b" * 64,
                "mac": "hmac-sha256:" + "c" * 64,
            }
            promotion = write_record("prover-promotion", {
                "schema": "zkdeal/oci-promotion-envelope/v1",
                "payload": promotion_payload,
                "authentication": promotion_auth,
            })
            records["proverRuntimePublication"] = promotion
            records["proverRuntimePromotionVerification"] = write_record("prover-promotion-verification", {
                "verified": True,
                "receiptSha256": promotion["sha256"],
                "keyId": promotion_auth["keyId"],
                "sourceImmutableReference": staged_images["runtime"],
                "promotedImmutableReference": promoted_ref,
                "daemonImageId": daemon_id,
                "exactDigestPreserved": True,
                "sameDaemonImageId": True,
            })
            physical_path = state / "physical.json"
            physical_path.write_text(json.dumps({
                "schemaVersion": 1,
                "candidateId": "candidate-physical",
                "candidateDescriptorSha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
                "passed": True,
                "records": records,
            }), encoding="utf-8")

            artifacts = {}
            artifact_paths = {
                "yellowPaperSourceManifest": "publication/yellow-source.json",
                "yellowPaperPdf": "publication/yellow-paper.pdf",
                "yellowPaperQaReport": "publication/yellow-qa.json",
                "investorDeckPptx": "outputs/zkdeal-investor-deck-business-model-v3-2026-08.pptx",
                "investorDeckQaReport": "publication/deck-qa.json",
            }
            for name, relative in artifact_paths.items():
                path = umbrella / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"artifact:{name}".encode())
                artifacts[name] = {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            publication_path = state / "publication.json"
            publication_path.write_text(json.dumps({
                "schemaVersion": 1,
                "candidateId": "candidate-physical",
                "candidateDescriptorSha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
                "physicalEvidenceManifestSha256": hashlib.sha256(physical_path.read_bytes()).hexdigest(),
                "passed": True,
                "gates": {name: True for name in candidate.REQUIRED_PUBLICATION_GATES},
                "artifacts": artifacts,
            }), encoding="utf-8")

            with patch.object(candidate, "ROOT", project):
                result = candidate.validate_physical_evidence(
                    candidate_value, candidate_path, physical_path,
                )
                self.assertEqual(set(result["records"]), candidate.REQUIRED_PHYSICAL_RECORDS)
                self.assertEqual(result["trustChain"]["promotedRuntime"], promoted_ref)
                published = candidate.validate_publication(
                    candidate_value, candidate_path, physical_path, publication_path,
                )
                self.assertEqual(set(published["artifacts"]), candidate.REQUIRED_PUBLICATION_ARTIFACTS)

                broken = json.loads(publication_path.read_text(encoding="utf-8"))
                broken["gates"]["investorDeckOverflow"] = False
                publication_path.write_text(json.dumps(broken), encoding="utf-8")
                with self.assertRaisesRegex(DeploymentError, "failed gates"):
                    candidate.validate_publication(
                        candidate_value, candidate_path, physical_path, publication_path,
                    )

                tampered = json.loads((umbrella / promotion["path"]).read_text(encoding="utf-8"))
                tampered["payload"]["sameDaemonImageId"] = False
                (umbrella / promotion["path"]).write_text(json.dumps(tampered), encoding="utf-8")
                with self.assertRaisesRegex(DeploymentError, "SHA-256 differs"):
                    candidate.validate_physical_evidence(candidate_value, candidate_path, physical_path)

    def test_owner_phase_rejects_joint_evidence_source_drift_before_regeneration(self):
        candidate_id = "candidate-source-drift"
        token = "sha256:" + "2" * 64
        gates = {name: True for name in candidate.REQUIRED_OWNER_GATES}
        with tempfile.TemporaryDirectory() as folder:
            umbrella = Path(folder)
            files = {
                "hosted-service-capabilities": "owner/hosted.json",
                "headless-room-node-capabilities": "owner/headless.json",
                "room-batch-hosted-integration-evidence": "owner/joint.json",
            }
            for relative in files.values():
                path = umbrella / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            (umbrella / files["hosted-service-capabilities"]).write_text(json.dumps({
                "managedL1Operations": {"roomBatch": {"hostedIntegration": {
                    "acceptanceToken": token,
                }}},
            }), encoding="utf-8")
            source_hashes = {name: "1" * 64 for name in files}
            evidence = {
                "schemaVersion": 1,
                "passed": True,
                "candidateId": candidate_id,
                "gateContractSha256": candidate.OWNER_GATE_CONTRACT_SHA256,
                "gates": gates,
                "hostedIntegrationAcceptanceToken": token,
                "sourceArtifacts": source_hashes,
            }
            evidence_path = umbrella / "owner/broad.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            value = {
                "candidateId": candidate_id,
                "ownerBroadSeal": {
                    "passed": True,
                    "evidenceManifestPath": "owner/broad.json",
                    "evidenceSha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                    "gateContractSha256": candidate.OWNER_GATE_CONTRACT_SHA256,
                    "hostedIntegrationAcceptanceToken": token,
                    "gates": gates,
                },
                "sourceArtifacts": source_hashes,
            }
            rows = [
                {"id": name, "path": relative, "required": True, "sha256": source_hashes[name]}
                for name, relative in files.items()
            ]
            with patch.object(
                candidate, "inventory", return_value={"umbrellaRoot": folder, "artifacts": rows},
            ), patch.object(
                candidate, "hosted_integration_evidence_errors", return_value=["source hash differs"],
            ), self.assertRaisesRegex(DeploymentError, "not source-bound to current bytes"):
                candidate.validate_owner_seal(value)


if __name__ == "__main__":
    unittest.main()
