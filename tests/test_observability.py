from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = ROOT / "observability" / "grafana" / "dashboards"
METRIC_RE = re.compile(r"\bzkdeal_[a-zA-Z0-9_:]+")
DERIVED_SUFFIXES = ("_bucket", "_count", "_sum", "_created")
TYPE_DECLARATION_RE = re.compile(
    r"#\s*TYPE\s+(zkdeal_[a-zA-Z0-9_:]+)\s+(?:counter|gauge|histogram|summary)"
)
TYPESCRIPT_FAMILY_RE = re.compile(
    r"\b(?:gauge|counter|summary)\(\s*['\"](zkdeal_[a-zA-Z0-9_:]+)['\"]"
)
TYPESCRIPT_OBJECT_FAMILY_RE = re.compile(
    r"\bname\s*:\s*['\"](zkdeal_[a-zA-Z0-9_:]+)['\"]\s*,\s*type\s*:"
)


def owner_production_metric_names():
    """Extract metric declarations from owner runtime sources, never tests.

    A raw substring scan treats acceptance assertions and documentation as a
    published metric and can also put many megabytes of owner source into a
    unittest failure.  The owner implementations currently expose families
    through TypeScript `gauge`/`counter`/`summary` calls, explicit family
    objects, or Prometheus `# TYPE` declarations in Rust/string emitters.
    These patterns identify the production declaration without depending on a
    moving owner-specific capability schema.  The final broad seal still
    requires the owner's machine catalog plus live scrape equivalence.
    """
    owner_roots = [
        ROOT.parent / "web2-api/server/src",
        ROOT.parent / "app-node/packages/room-node/src",
        ROOT.parent / "prover-node",
    ]
    excluded_directories = {
        "target", "node_modules", ".git", "dist", "build", "out", "coverage",
        "test", "tests", "fixtures", "__snapshots__",
    }
    names = set()
    for root in owner_roots:
        for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name not in excluded_directories)
            for filename in sorted(filenames):
                path = Path(directory) / filename
                lower = path.name.lower()
                if path.suffix not in {".ts", ".rs"} or any(
                    marker in lower for marker in (".test.", ".spec.")
                ):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                names.update(TYPE_DECLARATION_RE.findall(text))
                names.update(TYPESCRIPT_FAMILY_RE.findall(text))
                names.update(TYPESCRIPT_OBJECT_FAMILY_RE.findall(text))
    return names


def catalog():
    return json.loads((ROOT / "observability/metric-catalog.json").read_text(encoding="utf-8"))


def metric_names(spec):
    return {
        metric["name"]
        for domain in spec["domains"].values()
        for metric in domain["metrics"]
    }


def canonical_metric(name, known):
    if name in known:
        return name
    for suffix in DERIVED_SUFFIXES:
        if name.endswith(suffix) and name[: -len(suffix)] in known:
            return name[: -len(suffix)]
    return name


class ObservabilityTests(unittest.TestCase):
    def test_every_required_domain_has_a_valid_dashboard_and_catalogued_queries(self):
        spec = catalog()
        known = metric_names(spec)
        required = {
            "l1-finality", "indexer", "wal-admission", "room", "proving-queue",
            "tenant-billing", "storage", "sponsorship", "blob", "withdrawal",
            "capacity", "aggregate", "transport",
        }
        self.assertEqual(required, set(spec["domains"]))
        uids = set()
        for domain, entry in spec["domains"].items():
            path = DASHBOARDS / entry["dashboard"]
            with self.subTest(domain=domain):
                dashboard = json.loads(path.read_text(encoding="utf-8"))
                self.assertGreaterEqual(dashboard["schemaVersion"], 41)
                self.assertNotIn(dashboard["uid"], uids)
                uids.add(dashboard["uid"])
                self.assertTrue(dashboard["panels"])
                for panel in dashboard["panels"]:
                    self.assertTrue(panel.get("title"))
                    for target in panel.get("targets", []):
                        for raw in METRIC_RE.findall(target.get("expr", "")):
                            self.assertIn(canonical_metric(raw, known), known, (path.name, raw))

    def test_metric_labels_are_low_cardinality_and_owner_status_is_explicit(self):
        spec = catalog()
        forbidden = set(spec["cardinalityPolicy"]["forbiddenLabels"])
        names = []
        for domain, entry in spec["domains"].items():
            for metric in entry["metrics"]:
                names.append(metric["name"])
                self.assertFalse(forbidden & set(metric["labels"]), (domain, metric["name"]))
                self.assertIn(metric["status"], {"owner-published", "standalone-only", "required-owner-contract"})
                self.assertIn(metric["type"], {"counter", "gauge", "histogram", "summary"})
                self.assertTrue(metric["unit"])
        self.assertEqual(len(names), len(set(names)))

    def test_owner_published_metrics_exist_and_required_contracts_remain_honest(self):
        spec = catalog()
        published = owner_production_metric_names()
        for domain in spec["domains"].values():
            for metric in domain["metrics"]:
                with self.subTest(metric=metric["name"]):
                    if metric["status"] in {"owner-published", "standalone-only"}:
                        self.assertIn(metric["name"], published)
                    else:
                        self.assertNotIn(
                            metric["name"], published,
                            "catalog status must be promoted only with the sealed owner metric contract",
                        )

    def test_renamed_series_never_reappear_in_catalog_dashboards_or_alerts(self):
        """Catalog names that drifted from the owner emission were aligned to
        the owner source of truth; the retired spellings must not come back."""
        retired = {
            "zkdeal_reorgs_total": "zkdeal_l1_reorgs_total",
            "zkdeal_post_finality_surprises_total": "zkdeal_l1_post_finality_surprises_total",
            "zkdeal_admission_wal_unacked": "zkdeal_admission_wal_records",
        }
        texts = {"metric-catalog.json": (ROOT / "observability/metric-catalog.json").read_text(encoding="utf-8")}
        for path in sorted(DASHBOARDS.glob("*.json")):
            texts[path.name] = path.read_text(encoding="utf-8")
        for filename in ("alerts.yml", "alerts.local.yml"):
            texts[filename] = (ROOT / "observability/prometheus" / filename).read_text(encoding="utf-8")
        known = metric_names(catalog())
        for old, new in retired.items():
            self.assertIn(new, known, new)
            for name, text in texts.items():
                with self.subTest(retired=old, file=name):
                    self.assertNotRegex(text, rf"\b{re.escape(old)}\b")

    def test_alerts_are_catalogued_and_linked_to_documented_runbooks(self):
        spec = catalog()
        known = metric_names(spec)
        runbook = (ROOT / "docs/monitoring-runbooks.md").read_text(encoding="utf-8").lower()
        alerts = []
        for filename in ("alerts.yml", "alerts.local.yml"):
            document = yaml.safe_load((ROOT / "observability/prometheus" / filename).read_text(encoding="utf-8"))
            for group in document["groups"]:
                alerts.extend(group["rules"])
        names = set()
        for alert in alerts:
            name = alert["alert"]
            self.assertNotIn(name, names)
            names.add(name)
            self.assertIn(alert["labels"].get("severity"), {"warning", "critical"})
            self.assertTrue(alert["labels"].get("service"))
            self.assertTrue(alert["labels"].get("unit"))
            self.assertTrue(alert["annotations"].get("summary"))
            url = alert["annotations"].get("runbook_url", "")
            self.assertRegex(url, r"^/guides/monitoring-runbooks\.md#[a-z0-9-]+$")
            self.assertIn(f"## {name}".lower(), runbook)
            for raw in METRIC_RE.findall(str(alert["expr"])):
                self.assertIn(canonical_metric(raw, known), known, (name, raw))


if __name__ == "__main__":
    unittest.main()
