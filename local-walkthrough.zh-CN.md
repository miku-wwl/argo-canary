# Argo Rollouts + ArgoCD + Istio（金丝雀）本地打通教程（Helm 版）

这份文档目标是：在一台本地 K3s 集群里，把本仓库的 Demo（Flask App + Helm Chart + ArgoCD Application）完整跑通，并验证 **Argo Rollouts + Istio traffic routing** 能正常工作。

> 关键点：ArgoCD 只会拉取 Git 仓库的内容。你在本地修改 Helm chart 后，必须 `commit + push`，集群里才会生效。

---

## 0. 你将得到什么

- `demo` 命名空间里部署 `demo-app`：
  - Argo Rollouts 管理副本与升级
  - Istio VirtualService 做流量拆分（stable/canary）
- ArgoCD Application：从 GitHub 仓库同步 Helm Chart
- Prometheus（可选）：用于 Rollouts 分析模板/指标（如果 chart 里启用）

---

## 1. 前置条件

- 一台 Linux VM（Ubuntu 22.04/24.04 都可以）跑 K3s，能访问公网拉镜像
- 你的本机（Windows）能：
  - `git` / `docker`（或 GitHub Actions 负责构建推送也行）
  - 能访问 GitHub（推送代码、ArgoCD 拉取）

建议版本（非强制）：
- K3s：v1.34.x
- Helm：v3.x
- Istio：1.29.x
- ArgoCD：stable manifests
- Argo Rollouts controller：稳定版

---

## 2. K3s 与 kubeconfig

在 VM 上：

```bash
sudo cat /etc/rancher/k3s/k3s.yaml
```

如果你用非 root 用户操作 `kubectl`，建议：

```bash
sudo chmod 644 /etc/rancher/k3s/k3s.yaml
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl get nodes -o wide
```

---

## 3. 安装 Istio（base / istiod / ingressgateway）

下面用 Helm 安装（命名空间 `istio-system`）：

```bash
helm repo add istio https://istio-release.storage.googleapis.com/charts
helm repo update

kubectl create namespace istio-system --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install istio-base istio/base -n istio-system
helm upgrade --install istiod istio/istiod -n istio-system --wait
helm upgrade --install istio-ingress istio/gateway -n istio-system --wait
```

K3s 通常没有云 LB，因此 ingressgateway 可能没有 ExternalIP。用 NodePort 访问：

```bash
kubectl -n istio-system get svc istio-ingress -o wide
```

记下 `80/TCP` 对应的 NodePort（后面访问 demo 用）。

---

## 4. 安装 Argo Rollouts（Controller + kubectl 插件可选）

Controller（官方清单方式，稳定）：

```bash
kubectl create namespace argo-rollouts --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
kubectl -n argo-rollouts rollout status deployment/argo-rollouts
```

可选：安装 `kubectl-argo-rollouts` CLI（便于看流量、步骤）。

---

## 5. 安装 ArgoCD

```bash
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deployment/argocd-server
```

取初始密码：

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
```

本地访问 ArgoCD（VM 上做 port-forward）：

```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443 --address 0.0.0.0
```

然后在你本机浏览器打开：`https://<VM_IP>:8080`（自签证书提示忽略）。

---

## 6. 安装 Prometheus（可选但推荐）

如果你要跑 Rollouts 的分析（AnalysisTemplate）或看 Istio 指标，建议装 kube-prometheus-stack：

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  -n monitoring \
  --set grafana.enabled=false \
  --set alertmanager.enabled=false
```

---

## 7. 准备 demo 命名空间（开启 Istio sidecar 注入）

```bash
kubectl create namespace demo --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace demo istio-injection=enabled --overwrite
```

---

## 8. 让 ArgoCD 从 GitHub 同步 Helm Chart

### 8.1 确认 Application 指向你的仓库

本仓库提供了 ArgoCD Application 清单：

- argo-canary-demo-helm-main/argocd/application.yaml

你需要确保：
- `repoURL` 指向你自己的 GitHub 仓库（例如 `https://github.com/<you>/argo-canary.git`）
- `path` 指向 Helm chart 目录（例如 `argo-canary-demo-helm-main/demo-app`）

然后应用它：

```bash
kubectl apply -f argo-canary-demo-helm-main/argocd/application.yaml
```

### 8.2 重要：stableService 不能为空

如果你启用了 Istio traffic routing（`trafficRouting.istio`），Rollout 规范要求 **必须**同时设置：
- `spec.strategy.canary.canaryService`
- `spec.strategy.canary.stableService`

否则 Rollout 会直接 `InvalidSpec`，ArgoCD Application Health 会 `Degraded`。

本仓库 Helm chart 已做了最小修补：把 `stableService` 固定为 `demo-app-stable`，保证该字段不会为空。

---

## 9. 验证：ArgoCD 同步 + Rollout Healthy

看 ArgoCD Application 状态：

```bash
kubectl -n argocd get application demo-app
```

看 Rollout：

```bash
kubectl -n demo get rollout demo-app
kubectl -n demo describe rollout demo-app | sed -n '1,200p'
```

看 Pod/RS：

```bash
kubectl -n demo get pods -l app=demo-app -o wide
kubectl -n demo get rs -o wide
```

预期：
- `application/demo-app` 变为 `Synced` + `Healthy`
- `rollout/demo-app` Desired=2 Available=2
- Pod 为 `2/2 Running`（1 个 app 容器 + 1 个 istio-proxy sidecar）

---

## 10. 从集群外访问 demo-app（Istio Gateway + Host Header）

Chart 会创建：Gateway + VirtualService，host 包含 `demo-app.mario.com`。

1) 先拿到 ingressgateway NodePort（HTTP 80 对应）：

```bash
kubectl -n istio-system get svc istio-ingress -o jsonpath='{.spec.ports[?(@.port==80)].nodePort}'; echo
```

2) 在你本机请求（把 `<VM_IP>` 换成你的 K3s VM IP，`<NODEPORT>` 换成上一步输出）：

```bash
curl -H 'Host: demo-app.mario.com' http://<VM_IP>:<NODEPORT>/
```

如果你的 app 暴露了版本信息/hostname，多请求几次通常能看到响应。

---

## 11. 触发一次发布（可选）

如果你已经在 GitHub Actions 配置了构建推送：
- workflow 会推送镜像：`minglai/argo-canary-demo-app:v${run_number}` 和 `:latest`
- 并可能回写 Helm values / Chart 版本（取决于你的 workflow 逻辑）

触发方式：
- push 一次应用代码
- 或手动触发 workflow

然后 ArgoCD 会拉到新的 chart/values，Rollout 会按步骤推进。

---

## 12. 常见问题排查

### 12.1 ArgoCD 一直 Degraded / Rollout InvalidSpec

- 重点看：

```bash
kubectl -n demo get rollout demo-app -o yaml | sed -n '1,220p'
```

- 如果看到 `spec.strategy.stableService` 为空或缺失：
  - 说明 ArgoCD 拉取的 Git revision 还没包含修补
  - 确认你本地修改已 `commit + push`
  - 然后强制 ArgoCD 刷新：

```bash
kubectl -n argocd annotate application demo-app argocd.argoproj.io/refresh=hard --overwrite
```

### 12.2 Istio ingress 没有 External IP

- K3s 裸机环境常见
- 用 NodePort 访问（见第 10 节）

### 12.3 Pod 一直 1/2 或 Init 卡住

- 先看是否在拉镜像：

```bash
kubectl -n demo describe pod -l app=demo-app | sed -n '1,220p'
```

- 常见原因：镜像拉取慢/失败、DNS、代理、或 registry 限速。

---

## 13. 清理（可选）

```bash
kubectl delete ns demo
kubectl delete ns argocd
kubectl delete ns argo-rollouts
kubectl delete ns monitoring
kubectl delete ns istio-system
```

> 如果你想保留基础组件（Istio/ArgoCD/Prometheus）做后续实验，只删 `demo` 即可。
