"""Regression tests for routing, analysis and Helm revision safety."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "infra/helm/demo-app"
pytestmark = pytest.mark.skipif(not shutil.which("helm"), reason="Helm is required")


def render(*overrides, chart=CHART):
    command = ["helm", "template", "demo-app", str(chart), "-n", "regression"]
    for override in overrides:
        command.extend(["--set", override])
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    return {(doc["kind"], doc["metadata"]["name"]): doc
            for doc in yaml.safe_load_all(result.stdout) if doc}


def test_canary_service_ownership_and_real_l7_steps():
    docs = render()
    rollout = docs["Rollout", "demo-app"]
    canary = rollout["spec"]["strategy"]["canary"]
    assert canary["trafficRouting"]["istio"]["virtualService"]["routes"] == ["http"]
    assert [step["setWeight"] for step in canary["steps"] if "setWeight" in step] == [10, 50, 100]
    assert sum("analysis" in step for step in canary["steps"]) == 2
    assert ("Service", "demo-app") not in docs
    for suffix in ("stable", "canary"):
        selector = docs["Service", "demo-app-" + suffix]["spec"]["selector"]
        assert selector == rollout["spec"]["selector"]["matchLabels"]
        assert "rollouts-pod-template-hash" not in selector
    assert all(doc["metadata"].get("namespace") != "demo" for doc in docs.values())


def test_analysis_requires_candidate_hash_volume_and_finite_budget():
    docs = render()
    metrics = docs["AnalysisTemplate", "demo-app-health"]["spec"]["metrics"]
    assert {m["name"] for m in metrics} == {"minimum-volume", "success-rate", "p95-latency"}
    for metric in metrics:
        assert metric["count"] > metric["inconclusiveLimit"]
        assert "len(result) == 1" in metric["successCondition"]
        query = metric["provider"]["prometheus"]["query"]
        assert 'rollout_hash="{{args.latest-hash}}"' in query
        if metric["name"] == "minimum-volume":
            assert metric["failureCondition"] == "false"
        else:
            assert "and on()" in query and ">= 50" in query
            assert "!isNaN(result[0])" in metric["failureCondition"]
    monitor = docs["ServiceMonitor", "demo-app-istio"]
    assert monitor["spec"]["namespaceSelector"]["matchNames"] == ["regression"]


def test_manual_and_bluegreen_paths_have_real_gates():
    docs = render("canary.manualApproval=true")
    assert {"pause": {}} in docs["Rollout", "demo-app"]["spec"]["strategy"]["canary"]["steps"]
    docs = render("deploymentStrategy=blue-green")
    strategy = docs["Rollout", "demo-app"]["spec"]["strategy"]["blueGreen"]
    assert strategy["autoPromotionEnabled"] is False
    assert ("Service", "demo-app-active") in docs and ("Service", "demo-app-preview") in docs
    assert ("Service", "demo-app-canary") not in docs
    assert "prePromotionAnalysis" in strategy


def test_non_workload_values_and_chart_metadata_do_not_change_pod_template(tmp_path):
    baseline = render()["Rollout", "demo-app"]["spec"]["template"]
    for setting in ("app.host=changed.local", "analysis.p95Milliseconds=400", "canary.manualApproval=true"):
        assert render(setting)["Rollout", "demo-app"]["spec"]["template"] == baseline
    modified = tmp_path / "chart"
    shutil.copytree(CHART, modified)
    metadata = yaml.safe_load((modified / "Chart.yaml").read_text())
    metadata.update(version="9.9.9", appVersion="metadata-only")
    (modified / "Chart.yaml").write_text(yaml.safe_dump(metadata))
    assert render(chart=modified)["Rollout", "demo-app"]["spec"]["template"] == baseline


def test_digest_and_fault_configuration_create_isolated_pod_revisions():
    baseline = render()["Rollout", "demo-app"]["spec"]["template"]
    faulty = render("app.faultMode=latency")["Rollout", "demo-app"]["spec"]["template"]
    assert faulty != baseline
    assert "envFrom" not in faulty["spec"]["containers"][0]
    assert not any("configMap" in v for v in faulty["spec"].get("volumes", []))
    digest = "sha256:" + "a" * 64
    image = render("image.digest=" + digest)["Rollout", "demo-app"]["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image.endswith("@" + digest)


def test_hpa_owns_replicas_and_targets_rollout_scale_subresource():
    docs = render("autoscaling.enabled=true")
    assert "replicas" not in docs["Rollout", "demo-app"]["spec"]
    target = docs["HorizontalPodAutoscaler", "demo-app"]["spec"]["scaleTargetRef"]
    assert target == {"apiVersion": "argoproj.io/v1alpha1", "kind": "Rollout", "name": "demo-app"}


def test_schema_rejects_unknown_strategy_and_fault_mode():
    for setting in ("deploymentStrategy=bogus", "app.faultMode=random"):
        result = subprocess.run(["helm", "template", "test", str(CHART), "--set", setting],
                                text=True, capture_output=True)
        assert result.returncode != 0
