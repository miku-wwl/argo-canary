# Demo Helm chart

This directory contains the Helm chart and Argo CD entry point for the Argo Canary demo. It consumes an externally supplied, conformant Kubernetes cluster and is Kubernetes-distribution/provider agnostic.

The chart depends on these platform capabilities:

- Argo CD and its `Application` CRD/controller
- Argo Rollouts and its `Rollout`/`AnalysisTemplate` CRDs/controller
- Istio and its `Gateway`/`VirtualService` CRDs/controller
- A Prometheus-compatible metrics source scraping Istio request metrics

It does not install or manage a cluster, infer whether the cluster is kind/K3s/AKS/EKS/GKE, or require a node IP.

## Preflight the target cluster

`kubectl` must already be installed and its current context must point to the target cluster:

```shell
kubectl config current-context
kubectl cluster-info
kubectl get nodes
kubectl get crd applications.argoproj.io rollouts.argoproj.io \
  virtualservices.networking.istio.io gateways.networking.istio.io \
  servicemonitors.monitoring.coreos.com
kubectl -n argo-rollouts get deployment argo-rollouts
kubectl -n argocd get deployment argocd-server argocd-repo-server
kubectl -n argocd get statefulset argocd-application-controller
kubectl -n istio-system get deployment istiod
kubectl -n monitoring get service prometheus-kube-prometheus-prometheus
```

These are capability checks, not distribution detection. The Prometheus service, ServiceMonitor namespace/labels, and Istio gateway selector are configurable in `demo-app/values.yaml` when the external platform uses different installation conventions.

## Render and bootstrap GitOps

Render the chart locally without touching the cluster:

```shell
helm lint demo-app
helm template demo-app ./demo-app
```

The canonical portfolio path is:

```text
GitHub Actions -> container registry -> GitOps image-tag update
  -> Argo CD -> Argo Rollouts -> Istio -> Prometheus analysis
```

Bootstrap the Argo CD Application from the repository root:

```shell
kubectl apply -f argo-canary-demo-helm-main/argocd/application.yaml
kubectl -n argocd get application demo-app
```

The Application tracks `argo-canary-demo-helm-main/demo-app`. Do not replace this with direct workload `kubectl apply`, `kind load docker-image`, or a cluster-creation command. Docker + kind is a recommended local reference environment. Reference environment only — not part of the project architecture; its setup is external to this chart.

## Resources and rollout contract

The chart manages:

- the application Rollout and stable/canary Services;
- an Istio `VirtualService` and `Gateway`;
- the Istio metrics Service and `ServiceMonitor`;
- the Prometheus-backed `AnalysisTemplate`;
- the retained blue/green configuration; and
- supporting Argo CD access configuration used by the demo.

In canary mode, the rendered Rollout must retain:

```yaml
canaryService: demo-app-canary
stableService: demo-app-stable
trafficRouting:
  istio:
    virtualService:
      name: demo-app
      routes:
        - http
```

The default steps remain `10% -> analysis -> 50% -> analysis -> 100%`. The `AnalysisTemplate` queries Istio metrics from `prometheus.analysisAddress` and uses the canary service name and namespace as runtime arguments.

## Inspect and access

```shell
kubectl -n demo get rollout demo-app
kubectl -n demo get service demo-app-stable demo-app-canary
kubectl -n demo get virtualservice demo-app -o yaml
kubectl -n demo get analysistemplate istio-success-rate
kubectl -n demo port-forward svc/demo-app 8081:80
```

Ingress and external access are environment-specific: local environments may use port-forward or a locally configured Istio ingress path, while managed platforms provide their own LoadBalancer/ingress implementation. No access path is hard-coded into the deployment architecture.

For a manual promotion experiment, temporarily replace the automated `canary.rollout.steps` values with a step containing `pause: {}`, then use `kubectl argo rollouts promote`. Restore the automated steps afterward so Prometheus analysis remains the default behavior.
