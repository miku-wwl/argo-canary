"""Repeatable isolated kind experiments. Runtime output stays outside tracked files."""
import argparse
import collections
import http.client
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import threading
import time
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]
VALUES = ROOT / "infra/helm/demo-app/values-lab.yaml"


def command(*args, input=None):
    result = subprocess.run(args, cwd=ROOT, input=input, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)}\n{result.stderr or result.stdout}")
    return result.stdout.strip()


class Traffic:
    def __init__(self, url, host, rate=50, workers=12):
        self.url = urlparse(url)
        self.host, self.rate, self.workers = host, rate, workers
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.counts = collections.Counter()
        self.codes = collections.Counter()
        self.latencies = collections.defaultdict(list)
        self.threads = []

    def worker(self):
        connection = None
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                if connection is None:
                    connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=5)
                connection.request("GET", self.url.path or "/", headers={"Host": self.host})
                response = connection.getresponse()
                body = response.read()
                try:
                    release = json.loads(body).get("release", "unknown")
                except ValueError:
                    release = "unknown"
                with self.lock:
                    self.counts[release] += 1
                    self.codes[response.status] += 1
                    self.latencies[release].append((time.monotonic() - started) * 1000)
            except (OSError, ValueError, http.client.HTTPException):
                if connection:
                    connection.close()
                connection = None
                with self.lock:
                    self.codes["transport-error"] += 1
            self.stop.wait(max(0, self.workers / self.rate - (time.monotonic() - started)))
        if connection:
            connection.close()

    def __enter__(self):
        for _ in range(self.workers):
            thread = threading.Thread(target=self.worker, daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def snapshot(self):
        with self.lock:
            return self.counts.copy()

    def summary(self):
        with self.lock:
            return {"requests_by_release": dict(self.counts), "http_status": dict(self.codes),
                    "latency_ms": {release: {"median": round(statistics.median(values), 2),
                        "p95": round(sorted(values)[math.ceil(len(values) * .95) - 1], 2)}
                        for release, values in self.latencies.items() if values}}

    def __exit__(self, *_):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=6)


class Lab:
    def __init__(self, args):
        self.args = args
        if args.context != "kind-argo-canary-local":
            raise RuntimeError("Destructive experiments require the isolated kind-argo-canary-local context")
        self.k("get", "nodes")
        self.start = time.monotonic()

    def log(self, event, **data):
        print(json.dumps({"elapsed_s": round(time.monotonic() - self.start, 1),
                          "event": event, **data}, ensure_ascii=False), flush=True)

    def k(self, *args, input=None):
        return command("kubectl", "--context", self.args.context, *args, input=input)

    def obj(self, kind, name="demo-app", namespace="demo"):
        return json.loads(self.k("-n", namespace, "get", kind, name, "-o", "json"))

    def rollout(self):
        return self.obj("rollout")

    def weights(self):
        routes = self.obj("virtualservice")["spec"]["http"][0]["route"]
        return [route.get("weight", 0) for route in routes]

    def wait(self, description, predicate, timeout=240):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                self.log(description)
                return value
            time.sleep(2)
        raise TimeoutError(description)

    def refresh(self):
        self.k("-n", "argocd", "annotate", "application", "demo-app",
               "argocd.argoproj.io/refresh=hard", "--overwrite")

    def sync(self, healthy=True):
        expected = command("git", "rev-parse", "HEAD")
        def status():
            app = self.obj("application", namespace="argocd").get("status", {})
            return app.get("sync", {}).get("revision") == expected and app.get("sync", {}).get("status") == "Synced" and (
                not healthy or app.get("health", {}).get("status") == "Healthy")
        self.wait("Argo CD Synced" + (" / Healthy" if healthy else ""), status)

    def healthy(self):
        self.wait("Rollout Healthy", lambda: self.rollout().get("status", {}).get("phase") == "Healthy")
        self.sync()

    def release(self, label, *, new_revision=True, **changes):
        if command("git", "status", "--porcelain", "--", str(VALUES)):
            raise RuntimeError("Preserve existing values-lab.yaml changes before running experiments")
        values = yaml.safe_load(VALUES.read_text(encoding="utf-8")) or {}
        if new_revision:
            values.setdefault("app", {}).update(release=f"{label}-{time.time_ns() // 1000000}",
                                                 faultMode="healthy")
        for key, value in changes.items():
            section, item = key.split("__", 1)
            values.setdefault(section, {})[item] = value
        VALUES.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        command("git", "add", str(VALUES))
        command("git", "commit", "-m", f"lab: {label}")
        command("git", "push")
        self.refresh()
        sha = command("git", "rev-parse", "HEAD")
        release = values["app"]["release"]
        self.log("GitOps release", commit=sha, release=release)
        self.wait("candidate reconciled", lambda: any(
            e["name"] == "APP_RELEASE" and e.get("value") == release
            for e in self.rollout()["spec"]["template"]["spec"]["containers"][0]["env"]))
        return sha, release

    def revert(self, sha):
        command("git", "revert", "--no-edit", sha)
        command("git", "push")
        self.refresh()
        self.healthy()

    def cli(self, operation):
        executable = shutil.which("kubectl-argo-rollouts") or shutil.which("kubectl-argo-rollouts.exe")
        if not executable:
            raise RuntimeError("Install the pinned kubectl-argo-rollouts plugin")
        action = [operation, "rollout"] if operation == "retry" else [operation]
        self.log("manual " + operation, output=command(executable, *action, "demo-app",
                 "--context", self.args.context, "-n", "demo"))

    def analysis_runs(self):
        runs = json.loads(self.k("-n", "demo", "get", "analysisruns", "-o", "json"))["items"]
        current = self.rollout().get("status", {}).get("currentPodHash")
        return [r for r in runs if r["metadata"].get("labels", {}).get("rollouts-pod-template-hash") == current]

    def analyses(self):
        return [{"name": r["metadata"]["name"], "phase": r.get("status", {}).get("phase"),
                 "metrics": [{"name": m["name"], "phase": m.get("phase"),
                    "values": [x.get("value") for x in m.get("measurements", [])]}
                    for m in r.get("status", {}).get("metricResults", [])]}
                for r in self.analysis_runs()]

    def prom(self, query):
        with urlopen(self.args.prometheus + "/api/v1/query?" + urlencode({"query": query}), timeout=8) as response:
            result = json.load(response)
        if result["status"] != "success":
            raise RuntimeError(str(result))
        return result["data"]["result"]

    def selectors(self):
        def proxies_ready():
            pods = json.loads(self.k("-n", "demo", "get", "pods", "-l", "app=demo-app", "-o", "json"))["items"]
            return pods and all(any(c["name"] == "istio-proxy" and c["ready"]
                for c in (p.get("status", {}).get("containerStatuses", []) +
                          p.get("status", {}).get("initContainerStatuses", [])))
                for p in pods if not p["metadata"].get("deletionTimestamp"))
        self.wait("application proxies Ready", proxies_ready)
        rollout = self.rollout()["status"]
        stable = self.obj("service", "demo-app-stable")["spec"]["selector"]["rollouts-pod-template-hash"]
        canary = self.obj("service", "demo-app-canary")["spec"]["selector"]["rollouts-pod-template-hash"]
        assert stable == rollout["stableRS"] and canary == rollout["currentPodHash"] and stable != canary
        pods = json.loads(self.k("-n", "demo", "get", "pods", "-l", "app=demo-app", "-o", "json"))["items"]
        for pod in pods:
            statuses = pod["status"].get("containerStatuses", []) + pod["status"].get("initContainerStatuses", [])
            assert any(c["name"] == "istio-proxy" and c["ready"] for c in statuses)
        slices = json.loads(self.k("-n", "demo", "get", "endpointslices", "-o", "json"))["items"]
        for suffix, expected in (("stable", stable), ("canary", canary)):
            endpoints = [e for s in slices if s["metadata"]["labels"].get("kubernetes.io/service-name") == "demo-app-" + suffix
                         for e in s["endpoints"] if e.get("conditions", {}).get("ready")]
            assert endpoints and all(expected in e["targetRef"]["name"] for e in endpoints)
        self.log("Stable/Canary selectors, endpoints and ready proxies verified", stable=stable, canary=canary)

    def load(self):
        return Traffic(self.args.url, self.args.host)

    def automatic(self):
        self.healthy()
        with self.load() as load:
            _, candidate = self.release("healthy", canary__manualApproval=False)
            buckets = collections.defaultdict(collections.Counter)
            previous_weights, previous_counts = None, load.snapshot()
            checked = False
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                weights, counts = self.weights(), load.snapshot()
                if weights != previous_weights:
                    self.log("route transition", weights=weights)
                if weights == previous_weights and weights in ([90, 10], [50, 50]):
                    buckets[weights[1]].update(counts - previous_counts)
                if weights == [90, 10] and not checked:
                    self.selectors()
                    checked = True
                if checked and self.rollout()["status"].get("phase") == "Healthy":
                    break
                previous_weights, previous_counts = weights, counts
                time.sleep(2)
            else:
                raise TimeoutError("automatic promotion")
            for weight in (10, 50):
                counts = buckets[weight]
                total = sum(counts.values())
                fraction = counts[candidate] / total if total else 0
                assert total >= 500 and abs(fraction - weight / 100) < .075, (weight, counts)
                self.log("measured weighted requests", configured_percent=weight, sample=total,
                         candidate_percent=round(fraction * 100, 2))
            runs = self.analyses()
            assert len(runs) == 2 and all(r["phase"] == "Successful" for r in runs), runs
            assert self.weights() == [100, 0]
            self.sync()
            self.log("EXP-01 PASS", analysis=runs, traffic=load.summary())
        self.wait("old ReplicaSets scaled down", lambda: sum(
            r.get("spec", {}).get("replicas", 0) for r in json.loads(
                self.k("-n", "demo", "get", "rs", "-l", "app=demo-app", "-o", "json"))["items"]
            if r["metadata"]["labels"].get("rollouts-pod-template-hash") != self.rollout()["status"]["stableRS"]) == 0)

    def failure(self, mode):
        self.healthy()
        with self.load() as load:
            sha, candidate = self.release(mode, app__faultMode=mode, canary__manualApproval=False)
            self.wait("automatic abort", lambda: self.rollout().get("status", {}).get("abort") is True)
            self.wait("Stable traffic restored", lambda: self.weights() == [100, 0])
            self.sync(healthy=False)
            app = self.obj("application", namespace="argocd")["status"]
            self.log("aborted desired state remains in Git", sync=app["sync"]["status"], health=app["health"]["status"])
            runs, traffic = self.analyses(), load.summary()
            assert any(r["phase"] == "Failed" for r in runs), runs
            assert traffic["requests_by_release"].get(candidate, 0) >= 50
            if mode == "http500":
                assert traffic["http_status"].get(500, 0) >= 50
                assert any(m["name"] == "success-rate" and m["phase"] == "Failed" for r in runs for m in r["metrics"])
            else:
                assert traffic["http_status"].get(500, 0) == 0, traffic
                assert traffic["latency_ms"][candidate]["median"] >= 780
                assert any(m["name"] == "p95-latency" and m["phase"] == "Failed" for r in runs for m in r["metrics"])
                assert any(m["name"] == "success-rate" and m["phase"] == "Successful" for r in runs for m in r["metrics"])
            self.log("EXP-03 PASS" if mode == "http500" else "EXP-04 PASS", analysis=runs, traffic=traffic)
            with self.load() as recovery:
                time.sleep(5)
            assert recovery.summary()["http_status"].get(500, 0) == 0
            self.revert(sha)
            self.replace_stable()
            self.log("EXP-07 PASS", reverted_commit=sha)

    def replace_stable(self):
        stable = self.rollout()["status"]["stableRS"]
        pods = json.loads(self.k("-n", "demo", "get", "pods", "-l",
            "rollouts-pod-template-hash=" + stable, "-o", "json"))["items"]
        old = pods[0]["metadata"]["name"]
        self.k("-n", "demo", "delete", "pod", old, "--wait=false")
        def ready():
            pods = json.loads(self.k("-n", "demo", "get", "pods", "-l",
                "rollouts-pod-template-hash=" + stable, "-o", "json"))["items"]
            fresh = [p for p in pods if p["metadata"]["name"] != old and not p["metadata"].get("deletionTimestamp")]
            return len(fresh) >= 2 and all(any(c["type"] == "Ready" and c["status"] == "True"
                for c in p["status"].get("conditions", [])) for p in fresh)
        self.wait("replacement Stable Pods Ready", ready)
        pods = json.loads(self.k("-n", "demo", "get", "pods", "-l",
            "rollouts-pod-template-hash=" + stable, "-o", "json"))["items"]
        for pod in pods:
            container = next(c for c in pod["spec"]["containers"] if c["name"] == "demo-app")
            assert next(e["value"] for e in container["env"] if e["name"] == "FAULT_MODE") == "healthy"
        with self.load() as traffic:
            time.sleep(3)
        assert set(traffic.summary()["http_status"]) == {200}
        self.log("healthy revision configuration verified after replacement")

    def manual(self):
        self.healthy()
        with self.load() as load:
            sha, candidate = self.release("manual", canary__manualApproval=True, canary__dynamicStableScale=True)
            def gate():
                rollout = self.rollout()["status"]
                return rollout.get("phase") == "Paused" and any(
                    p["reason"] == "CanaryPauseStep" for p in rollout.get("pauseConditions", []))
            self.wait("manual approval gate", gate)
            index = self.rollout()["status"]["currentStepIndex"]
            assert any(r["phase"] == "Successful" for r in self.analyses())
            time.sleep(35)
            assert gate() and self.rollout()["status"]["currentStepIndex"] == index
            self.log("indefinite gate did not auto-advance", observed_seconds=35)
            self.cli("abort")
            self.wait("manual abort restored Stable", lambda: self.weights() == [100, 0])
            self.cli("retry")
            self.wait("retry reached manual gate", gate)
            self.cli("promote")
            self.wait("second manual gate", lambda: gate() and self.rollout()["status"]["currentStepIndex"] > index)
            self.connection_comparison(candidate)
            self.cli("promote")
            self.healthy()
            self.log("EXP-02 PASS", traffic=load.summary(), analysis=self.analyses())
            self.revert(sha)

    def connection_comparison(self, candidate):
        """One temporary L4 Service and an unmeshed client; the app still uses Helm."""
        service = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "demo-app-l4", "namespace": "demo"},
                   "spec": {"selector": {"app": "demo-app"}, "ports": [{"port": 80, "targetPort": 5000}]}}
        program = '''import collections,http.client,json
results={}
for name,host,authority,reuse in [
    ("l4_reused","demo-app-l4","demo-app.local",True),
    ("l4_fresh","demo-app-l4","demo-app.local",False),
    ("istio_reused","istio-ingressgateway.istio-system","demo-app.local",True)]:
    counts=collections.Counter(); ports=set(); connection=None
    for _ in range(1000):
        if connection is None: connection=http.client.HTTPConnection(host,80,timeout=5)
        connection.request("GET","/",headers={"Host":authority})
        ports.add(connection.sock.getsockname()[1])
        response=connection.getresponse(); body=json.loads(response.read())
        assert response.status==200
        counts[body["release"]]+=1
        if not reuse: connection.close(); connection=None
    if connection: connection.close()
    results[name]={"releases":dict(counts),"local_ports":len(ports)}
print(json.dumps(results))
'''
        image = self.rollout()["spec"]["template"]["spec"]["containers"][0]["image"]
        pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "keepalive-client", "namespace": "demo",
               "annotations": {"sidecar.istio.io/inject": "false"}}, "spec": {"restartPolicy": "Never",
               "automountServiceAccountToken": False, "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
               "containers": [{"name": "client", "image": image, "command": ["python", "-c", program],
                 "resources": {"requests": {"cpu": "50m", "memory": "32Mi"}, "limits": {"memory": "96Mi"}}}]}}
        try:
            self.k("apply", "-f", "-", input=yaml.safe_dump_all([service, pod]))
            def completed():
                phase = self.obj("pod", "keepalive-client")["status"]["phase"]
                if phase == "Failed":
                    raise RuntimeError(self.k("-n", "demo", "logs", "keepalive-client"))
                return phase == "Succeeded"
            self.wait("keep-alive comparison finished", completed, timeout=180)
            results = json.loads(self.k("-n", "demo", "logs", "keepalive-client"))
            counts = results["l4_reused"]["releases"]
            assert len(counts) == 1 and results["l4_reused"]["local_ports"] == 1, results
            assert results["istio_reused"]["local_ports"] == 1, results
            for mode in ("l4_fresh", "istio_reused"):
                fraction = results[mode]["releases"].get(candidate, 0) / 1000
                assert abs(fraction - .5) < .075, results
            self.log("EXP-06 PASS", configured_l7_percent=50, results=results)
        finally:
            self.k("-n", "demo", "delete", "pod/keepalive-client", "service/demo-app-l4", "--ignore-not-found", "--wait=false")

    def no_data(self):
        self.healthy()
        sha, _ = self.release("no-traffic", canary__manualApproval=False)
        self.wait("bounded Inconclusive analysis", lambda: any(r["phase"] == "Inconclusive" for r in self.analyses()))
        status = self.rollout()["status"]
        assert status["phase"] == "Paused" and not status.get("abort", False)
        assert self.weights() == [90, 10]
        assert not any(r["phase"] == "Failed" for r in self.analyses())
        self.log("no-data safely paused", analysis=self.analyses(), pause_conditions=status.get("pauseConditions"))
        with self.load() as load:
            time.sleep(25)
            # v1.10 retry clears an Abort. It does not rerun an Inconclusive
            # step; promote would skip that analysis. Abort + Retry re-evaluates.
            self.cli("abort")
            self.wait("no-data recovery restored Stable", lambda: self.weights() == [100, 0])
            self.cli("retry")
            self.healthy()
            self.log("EXP-05 PASS", traffic=load.summary(), analysis=self.analyses())
            self.revert(sha)

    def configuration(self):
        self.healthy()
        original = self.rollout()["status"]["currentPodHash"]
        sha, _ = self.release("analysis-values-only", new_revision=False, analysis__p95Milliseconds=450)
        self.sync()
        assert self.rollout()["status"]["currentPodHash"] == original
        self.revert(sha)
        chart = ROOT / "infra/helm/demo-app/Chart.yaml"
        if command("git", "status", "--porcelain", "--", str(chart)):
            raise RuntimeError("Preserve Chart.yaml edits first")
        metadata = yaml.safe_load(chart.read_text())
        metadata.update(version="0.2.1", appVersion="metadata-only")
        chart.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
        command("git", "add", str(chart))
        command("git", "commit", "-m", "lab: Chart metadata-only safety")
        command("git", "push")
        sha = command("git", "rev-parse", "HEAD")
        self.refresh()
        self.sync()
        assert self.rollout()["status"]["currentPodHash"] == original
        self.revert(sha)
        with self.load():
            sha, _ = self.release("pod-template-change")
            self.wait("Pod template created a new revision", lambda: self.rollout()["status"]["currentPodHash"] != original)
            self.healthy()
            self.revert(sha)
        for kind, name in (("rollout", "demo-app"), ("virtualservice", "demo-app"), ("service", "demo-app-stable")):
            annotation = self.obj(kind, name)["metadata"].get("annotations", {})
            assert "argocd.argoproj.io/tracking-id" in annotation
        assert self.weights() == [100, 0]
        self.log("EXP-09 PASS", unchanged_revision=original)

    def hpa(self):
        self.healthy()
        original = self.rollout()["status"]["currentPodHash"]
        with self.load():
            sha, _ = self.release("HPA", new_revision=False, autoscaling__enabled=True,
                                  autoscaling__targetCPUUtilizationPercentage=1)
            self.sync()
            def scaled():
                hpa = self.obj("hpa").get("status", {})
                active = any(c["type"] == "ScalingActive" and c["status"] == "True" for c in hpa.get("conditions", []))
                return active and hpa.get("currentReplicas", 0) >= 3 and self.rollout()["spec"]["replicas"] >= 3
            self.wait("HPA scaled the Rollout", scaled, timeout=180)
            assert self.rollout()["status"]["currentPodHash"] == original
            self.log("EXP-11 PASS", hpa=self.obj("hpa")["status"], scale=json.loads(
                self.k("get", "--raw", "/apis/argoproj.io/v1alpha1/namespaces/demo/rollouts/demo-app/scale")))
            self.revert(sha)
            self.wait("Git replica ownership restored", lambda: self.rollout()["spec"]["replicas"] == 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["healthy", "manual", "http500", "latency", "no-data", "configuration", "hpa", "load"])
    parser.add_argument("--context", default="kind-argo-canary-local")
    parser.add_argument("--url", default="http://127.0.0.1:18080/")
    parser.add_argument("--host", default="demo-app.local")
    parser.add_argument("--prometheus", default="http://127.0.0.1:19090")
    parser.add_argument("--duration", type=int, default=60)
    args = parser.parse_args()
    if args.scenario == "load":
        with Traffic(args.url, args.host) as traffic:
            time.sleep(args.duration)
        print(json.dumps(traffic.summary()))
        return
    lab = Lab(args)
    {"healthy": lab.automatic, "manual": lab.manual, "http500": lambda: lab.failure("http500"),
     "latency": lambda: lab.failure("latency"), "no-data": lab.no_data,
     "configuration": lab.configuration, "hpa": lab.hpa}[args.scenario]()


if __name__ == "__main__":
    main()
