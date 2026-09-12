# Argo Canary 本地参考环境

本项目是一个 Kubernetes-distribution-agnostic 的 GitOps 渐进式交付示例，依赖 Argo CD、Argo Rollouts、Istio 和 Prometheus-compatible metrics source。

本教程只使用已经存在的 Kubernetes 集群。项目不会创建、删除、切换或识别集群。Docker + kind 是推荐的本地参考环境。Reference environment only — not part of the project architecture；kind 集群的创建属于项目边界之外；K3s、AKS、EKS、GKE 或其他兼容 Kubernetes 的发行版也可以作为外部环境。

## 1. 前置条件

- 已安装 `kubectl` 和 Helm。
- `kubectl current-context` 已经指向目标集群。
- 外部平台已提供 Argo CD、Argo Rollouts、Istio 和 Prometheus-compatible metrics source。
- 集群允许拉取应用镜像，并且 GitHub/容器 registry 可访问。

先运行 capability-based prerequisite validation：

```shell
kubectl config current-context
kubectl cluster-info
kubectl get nodes
kubectl api-resources --api-group argoproj.io
kubectl api-resources --api-group networking.istio.io
kubectl api-resources --api-group monitoring.coreos.com
kubectl get crd applications.argoproj.io rollouts.argoproj.io \
  virtualservices.networking.istio.io gateways.networking.istio.io \
  servicemonitors.monitoring.coreos.com
kubectl -n argo-rollouts get deployment argo-rollouts
kubectl -n argocd get deployment argocd-server argocd-repo-server
kubectl -n argocd get statefulset argocd-application-controller
kubectl -n istio-system get deployment istiod
kubectl -n monitoring get service prometheus-kube-prometheus-prometheus
```

这些命令验证集群能力，不判断集群属于哪一种发行版。最后一条使用 chart 的默认 Prometheus Service 名称；如果外部平台命名不同或 Istio ingress gateway 使用其他 label，请在 Helm values 中设置 `prometheus.analysisAddress`、`prometheus.serviceMonitorNamespace`、`prometheus.serviceMonitorLabels` 和/或 `istio.gatewaySelector`。

## 2. 从 Argo CD Application 开始

创建应用 namespace（如果平台尚未创建）：

```shell
kubectl create namespace demo --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace demo istio-injection=enabled --overwrite
```

然后只提交 Argo CD Application 入口：

```shell
kubectl apply -f argo-canary-demo-helm-main/argocd/application.yaml
kubectl -n argocd get application demo-app
```

不要在主流程中执行 `kind create cluster`，也不要使用 `kind load docker-image` 或直接 `kubectl apply` 工作负载来替代 GitOps。应用发布路径保持为：

```text
Git Push -> GitHub Actions -> Container Registry -> GitOps Helm tag update
  -> Argo CD -> Argo Rollouts -> Istio -> Prometheus Analysis -> Promote/Abort
```

## 3. 验证 canary

```shell
kubectl -n demo get rollout demo-app
kubectl -n demo get service demo-app-stable demo-app-canary
kubectl -n demo get virtualservice demo-app -o yaml
kubectl -n demo get analysistemplate istio-success-rate
```

默认 canary 步骤为 `10% -> analysis -> 50% -> analysis -> 100%`。Rollout 同时声明 `stableService`、`canaryService` 和 Istio traffic routing；Prometheus AnalysisTemplate 查询 Istio request metrics，并在失败时阻止或回滚发布。

手动观察时可以使用：

```shell
kubectl argo rollouts get rollout demo-app -n demo -w
kubectl argo rollouts promote demo-app -n demo
kubectl argo rollouts abort demo-app -n demo
```

若要测试手动 promotion，把 `values.yaml` 中自动步骤临时替换为包含 `pause: {}` 的步骤；不要保留重复的 YAML `steps` 键。

## 4. 访问应用

访问方式由外部环境决定。Kind 或其他本地环境可以使用 port-forward：

```shell
kubectl -n demo port-forward svc/demo-app 8081:80
curl http://127.0.0.1:8081/
```

托管集群可以使用环境提供的 LoadBalancer 或 ingress。不要在 chart 或教程中写入节点 IP，也不要假设 NodePort、云厂商或某一种网络行为。

## 5. Helm 静态验证

```shell
helm lint argo-canary-demo-helm-main/demo-app
helm template demo-app argo-canary-demo-helm-main/demo-app
```

渲染结果应包含 Rollout、非空的 `stableService`/`canaryService`、`trafficRouting.istio`、`demo-app` VirtualService route，以及 `istio-success-rate` AnalysisTemplate。
