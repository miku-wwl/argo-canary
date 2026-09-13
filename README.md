# Argo Canary

Argo Canary is a Kubernetes-distribution-agnostic GitOps progressive-delivery project using Argo CD, Argo Rollouts, Istio, and Prometheus.

It demonstrates how an application moves through a canary release while Istio shifts traffic and Prometheus metrics automatically allow promotion or trigger an abort. The repository consumes an externally supplied Kubernetes cluster; it does not create, identify, or manage the cluster that runs it.

## What this project demonstrates

- GitHub Actions builds and pushes the application image to a container registry.
- A GitOps image-tag update changes the Helm desired state.
- Argo CD reconciles the Helm chart into the target cluster.
- Argo Rollouts manages stable/canary ReplicaSets and rollout steps.
- Istio routes weighted traffic through stable and canary Services.
- A Prometheus-backed `AnalysisTemplate` evaluates the canary before promotion.
- The default progression is `10% -> analysis -> 50% -> analysis -> 100%`.
- A blue/green configuration and an optional manual-promotion example are retained.

## Architecture and boundary

```text
External infrastructure
  Docker + kind / K3s / AKS / EKS / GKE / another conformant distribution
  configured kubectl context
                         |
                         | Kubernetes API
                         v
Argo Canary repository
  GitHub Actions -> container registry -> GitOps Helm update -> Argo CD
       -> Argo Rollouts -> Istio weighted traffic -> Prometheus analysis
       -> promote or abort
```

The accurate portability claim is: this project is Kubernetes-distribution/provider agnostic while depending on a defined set of Kubernetes platform capabilities.

### Dependency model

| Boundary | Responsibility |
| --- | --- |
| Required external environment | A conformant Kubernetes cluster and a configured `kubectl` context |
| Required platform capabilities | Argo CD, Argo Rollouts, Istio, and a Prometheus-compatible metrics source |
| Project-managed resources | Helm chart, Rollout, stable/canary Services, Istio `VirtualService`/`Gateway`, `AnalysisTemplate`, and Argo CD `Application` |
| Delivery ownership | GitHub Actions publishes the image and updates the GitOps Helm image tag; Argo CD reconciles the change |

No file in the canonical workflow provisions a cluster. The repository does not contain Kubernetes-distribution detection or provider-specific branches.

## Prerequisites and capability-based preflight

`kubectl` must already be installed, and `kubectl current-context` must point to a working target cluster. Docker + kind is the recommended local reference environment. Reference environment only — not part of the project architecture; creating it is outside this project boundary. Managed Kubernetes access is supplied by the relevant external platform.

Start every deployment from the configured context:

```shell
kubectl config current-context
kubectl cluster-info
kubectl get nodes
```

Then validate capabilities rather than trying to identify the Kubernetes distribution:

```shell
# API groups and CRDs used by this project
kubectl api-resources --api-group argoproj.io
kubectl api-resources --api-group networking.istio.io
kubectl api-resources --api-group monitoring.coreos.com
kubectl get crd \
  applications.argoproj.io \
  rollouts.argoproj.io \
  virtualservices.networking.istio.io \
  gateways.networking.istio.io \
  servicemonitors.monitoring.coreos.com

# Controllers and the metrics endpoint expected by the default chart values
kubectl -n argo-rollouts get deployment argo-rollouts
kubectl -n argocd get deployment argocd-server argocd-repo-server
kubectl -n argocd get statefulset argocd-application-controller
kubectl -n istio-system get deployment istiod
kubectl -n monitoring get service prometheus-kube-prometheus-prometheus
```

The namespace and platform service names above are chart defaults, not Kubernetes-distribution requirements. If the externally supplied platform uses different names or an alternate Istio ingress gateway label, set `prometheus.analysisAddress`, `prometheus.serviceMonitorNamespace`, `prometheus.serviceMonitorLabels`, and/or `istio.gatewaySelector` in the Helm values.

## Canonical GitOps workflow

1. Confirm the external cluster and platform capabilities with the preflight commands above.
2. Ensure the `demo` namespace is available and labelled for Istio sidecar injection where the platform requires it:

   ```shell
   kubectl create namespace demo --dry-run=client -o yaml | kubectl apply -f -
   kubectl label namespace demo istio-injection=enabled --overwrite
   ```

3. Bootstrap the Argo CD `Application` once from the repository:

   ```shell
   kubectl apply -f infra/argocd/application.yaml
   ```

   This applies only the Argo CD GitOps entry point. The application workload is rendered and reconciled by Argo CD from `infra/helm/demo-app`.
4. For a release, push application changes. GitHub Actions builds and pushes the image, then updates the Helm image tag in Git. Argo CD detects that GitOps change and starts the Rollout.
5. Observe reconciliation and rollout progress:

   ```shell
   kubectl -n argocd get application demo-app
   kubectl -n demo get rollout demo-app
   kubectl -n demo get virtualservice demo-app -o yaml
   ```

Do not replace this flow with `kind load docker-image` or direct workload `kubectl apply`. A local image-loading shortcut can be useful for experiments, but it is not the canonical delivery path.

## Rollout behavior

The default Helm values keep the existing Argo Rollouts + Istio integration:

- `canaryService: demo-app-canary`
- `stableService: demo-app-stable`
- `trafficRouting.istio.virtualService.name: demo-app`
- the `http` VirtualService route
- `10%`, pause, Prometheus analysis, `50%`, pause, Prometheus analysis

The `AnalysisTemplate` queries Istio request metrics from the configured Prometheus-compatible endpoint. A failed analysis counts against `failureLimit`; a successful analysis allows the rollout to continue. Argo CD ignores the Rollouts-controlled VirtualService route weights so reconciliation does not fight traffic progression.

For a manual-promotion experiment, temporarily replace the automated `canary.rollout.steps` in `infra/helm/demo-app/values.yaml` with a step containing `pause: {}`. Promote or abort with:

```shell
kubectl argo rollouts promote demo-app -n demo
kubectl argo rollouts abort demo-app -n demo
```

Set `deploymentStrategy: blue-green` to use the retained blue/green configuration; that mode is unchanged by this portability refactor.

## Access is environment-specific

The application architecture does not require a particular ingress implementation or node address.

For local development, use a port-forward or a locally configured Istio ingress path:

```shell
kubectl -n demo port-forward svc/demo-app 8081:80
curl http://127.0.0.1:8081/
```

On managed Kubernetes, an external LoadBalancer or ingress implementation is supplied by the environment. Hostnames in `values.yaml` are demo configuration and must resolve through the chosen environment's access path; no cloud provider or node IP is embedded in the chart.

## Validate the chart locally

From the repository root:

```shell
helm lint infra/helm/demo-app
helm template demo-app infra/helm/demo-app > rendered-demo-app.yaml
```

Inspect the rendered output for `kind: Rollout`, non-empty `stableService` and `canaryService`, `trafficRouting.istio`, the `demo-app` VirtualService route, and `kind: AnalysisTemplate`. `rendered-demo-app.yaml` is a local artifact and should not be committed.

## Repository layout

```text
argo-canary/
├── src/                         # Application source
├── tests/                       # Application tests
├── infra/
│   ├── helm/demo-app/            # Canonical Helm/platform resources
│   └── argocd/application.yaml   # GitOps entry point
├── examples/kustomize/           # Alternative/reference implementation
├── docs/                         # Extended docs and screenshots
├── .github/workflows/            # CI/CD and GitOps update automation
├── Dockerfile
├── requirements.txt
└── README.md
```

See [docs/kubernetes-client-and-preflight.md](docs/kubernetes-client-and-preflight.md) and [docs/kubernetes-local-reference.zh-CN.md](docs/kubernetes-local-reference.zh-CN.md) for extended guidance.
