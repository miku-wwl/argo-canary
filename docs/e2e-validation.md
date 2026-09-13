# 已验证的 E2E 渐进式交付验证

本文记录规范 GitOps 发布路径和一次受控失败金丝雀发布（Canary）的完整运行时验证结果。
内容来自本地参考环境中实际观测到的证据，不代表生产规模的容量或可用性承诺。

## 1. 成功的 GitOps 发布

已验证的发布链路如下：

```text
Git 推送
  -> GitHub Actions
  -> 容器镜像仓库
  -> 自动 GitOps Helm 镜像标签提交
  -> Argo CD 自动同步
  -> Argo Rollouts
  -> Istio 加权流量
  -> Prometheus 分析
  -> 自动晋级
  -> 健康发布
```

### 基线

源代码发布前立即观测到的基线如下：

| 项目 | 观测值 |
| --- | --- |
| 源代码提交 | `e0b39a0` (`e0b39a08f7cc5512f33cf6c8ed16df0a790991f9`) |
| Git 期望镜像 | `minglai/argo-canary-demo-app:v39` |
| Argo CD 修订版本 | `e0b39a08...` |
| Rollout 状态 | `Healthy` |
| 稳定 ReplicaSet | `5697d9fb5f` |
| 基线 HTTP 响应 | `200` |

### 源代码发布与 GitHub Actions

唯一的应用变更是 [`src/app.py`](../src/app.py) 中已有的响应标记：

```text
Hello Auckland k8s Demo - From version 50
  ->
Hello Auckland k8s Demo - From version 51
```

源代码发布提交为 `5e85c74`，已通过 HTTPS 正常推送到 `origin/main`。

实际工作流 [`构建并推送镜像`](../.github/workflows/build.yaml) 执行成功：

| 项目 | 观测值 |
| --- | --- |
| 运行编号 | [34732214346](https://github.com/miku-wwl/argo-canary/actions/runs/34732214346) |
| 结果 | `success` |
| 用时 | 31 秒 |
| 镜像 | `docker.io/minglai/argo-canary-demo-app:v40` |
| 镜像 digest | `sha256:617dd67b69dffb2e828eeaf6ac9576b8e21d6406c53a312a9399a8bb8c20a377` |

该工作流构建并推送了带版本号的镜像和 `latest` 标签，然后更新 GitOps 工作目录。
自动生成的 GitOps 提交为 `255d918`，将 [`values.yaml`](../infra/helm/demo-app/values.yaml)
中的镜像标签更新为 `v40`，并将 [`Chart.yaml`](../infra/helm/demo-app/Chart.yaml) 中的
`appVersion` 更新为 `40.0.0`。

工作流触发路径排除了 `infra/**`。因此，这个仅更新 GitOps 的提交没有再次触发镜像构建工作流。

### Argo CD 自动同步

Argo CD 的 `demo-app` Application 自动获取了该 GitOps 提交，过程中没有执行手动 Helm
升级，也没有直接修改运行中的 Rollout 镜像：

| 项目 | 观测值 |
| --- | --- |
| 之前的修订版本 | `e0b39a08...` |
| 新的修订版本 | `255d9181833efd2d544bc7bb4c017556e0723395` |
| 同步方式 | 自动；操作成功 |
| Application 最终状态 | `Synced / Healthy` |

Application 源路径为 `infra/helm/demo-app`，由
[`infra/argocd/application.yaml`](../infra/argocd/application.yaml) 定义。

### 运行时 Canary 证据

候选 ReplicaSet 为 `d796b94c5`。运行时观测到的顺序如下：

1. 候选版本 `10%` / 稳定版本 `90%`。
2. 第一个 AnalysisRun `demo-app-d796b94c5-6-2` 完成，状态为 `Successful`。
3. 候选版本 `50%` / 稳定版本 `50%`。
4. 第二个 AnalysisRun `demo-app-d796b94c5-6-5` 完成，状态为 `Successful`。
5. 两个 AnalysisRun 的成功测量值均为 `[1]`。
6. 候选版本晋级为 `100%` 稳定流量。

实际流量路径为：

```text
集群内负载生成器
  -> Istio ingress 网关
  -> demo-app VirtualService
  -> 稳定/Canary Service
  -> 应用 Pod
```

负载生成器使用 `demo-app.mario.com` Host 请求头，没有直接调用应用 Pod。

### 成功发布的最终状态

- Rollout：`Healthy`
- 候选流量：`0%`
- 稳定流量：`100%`
- Rollout 镜像：`minglai/argo-canary-demo-app:v40`
- 运行中 Pod 镜像：`v40`
- 运行中 Pod 镜像 digest：与上面的 GitHub Actions digest 匹配
- 经 Istio 的 HTTP 响应：`200`
- 响应正文：`Hello Auckland k8s Demo - From version 51`

## 2. 受控失败的金丝雀发布

失败测试使用了在仓库之外构建的临时镜像：

```text
argo-canary-local:broken-20260913-1234
```

该镜像会在根路径稳定返回 HTTP 500 响应。该镜像没有提交，也没有推送。

观测证据如下：

- 相同的 Istio ingress 路径返回了 7 次 HTTP `500`。
- AnalysisRun `demo-app-68f6c4c8d9-3-2` 完成，状态为 `Failed`。
- Prometheus 成功率测量值为 `0.9591` 和 `0.9010`。
- 配置的阈值为 `0.99`。
- Rollout 记录为 `Abort = true`、`Phase = Degraded`。
- 流量恢复为候选 `0%`、稳定 `100%`。
- 随后通过 ingress 路径发出的请求持续返回 HTTP `200`。

由于基于 Prometheus 的 AnalysisRun 低于配置的成功率阈值，失败候选版本没有继续晋级。
这次验证证明了该 Rollout 中基于指标的 Analysis 失败、Rollout 中止和稳定流量恢复；
但不代表系统具备本次观测范围之外的更广泛回滚能力。

随后，环境恢复为健康的候选版本：

- Rollout：`Healthy`
- `abort` 字段：已清除
- 候选流量：`0%`
- 稳定流量：`100%`

测试结束后，临时负载生成器、故障镜像构建上下文和损坏的 Docker 镜像均已移除。

## 3. Prometheus 查询

AnalysisTemplate 使用了下面这条已验证的查询：

```promql
sum(rate(istio_requests_total{
  reporter="destination",
  destination_service=~"demo-app-canary.demo.svc.cluster.local",
  destination_workload_namespace="demo",
  response_code!~"5.*"
}[2m]))
/
sum(rate(istio_requests_total{
  reporter="destination",
  destination_service=~"demo-app-canary.demo.svc.cluster.local",
  destination_workload_namespace="demo"
}[2m]))
```

健康候选版本返回 `1.0`。损坏候选版本产生了低于 `0.99` 的测量值，导致 AnalysisRun
失败并使 Rollout 中止。

指标来源是真实的 Istio 指标 `istio_requests_total`，观测到的目标标签为：

```text
destination_service=demo-app-canary.demo.svc.cluster.local
destination_workload_namespace=demo
```

## 4. 证据矩阵

| 能力 | 证据 | 结果 |
| --- | --- | --- |
| CI 镜像构建 | GitHub Actions 运行 `34732214346`，Build and Push 步骤成功 | PASS |
| 镜像仓库推送 | 带版本镜像 `v40` 及其 digest 已推送 | PASS |
| GitOps 提交 | 自动提交 `255d918` 更新了 Helm 标签和 Chart appVersion | PASS |
| Argo CD 自动同步 | Application 切换到修订版本 `255d918...`，状态为 `Synced / Healthy` | PASS |
| 10% 加权 Canary | 运行时观测到 Rollout 状态为 `10 / 90` | PASS |
| 第一次 Prometheus Analysis | `demo-app-d796b94c5-6-2`，测量值为 `[1]` | PASS |
| 50% 加权 Canary | 运行时观测到 Rollout 状态为 `50 / 50` | PASS |
| 第二次 Prometheus Analysis | `demo-app-d796b94c5-6-5`，测量值为 `[1]` | PASS |
| 自动晋级 | Rollout 到达第 6 步，稳定流量为 100% | PASS |
| 失败候选检测 | 7 次 HTTP 500，以及测量值 `0.9591`、`0.9010` | PASS |
| Rollout 中止 | `Abort=true`、`Phase=Degraded`，且 AnalysisRun 失败 | PASS |
| 稳定流量恢复 | 中止后的状态为候选 `0%`、稳定 `100%`，随后 HTTP 200 | PASS |
| 最终镜像一致性 | Git、Rollout、Pod 标签均为 `v40`；Pod digest 与 CI digest 匹配 | PASS |

## 5. 架构边界

项目的规范发布路径为：

```text
Git 推送
  -> GitHub Actions
  -> 容器镜像仓库
  -> GitOps
  -> Argo CD
  -> Argo Rollouts
  -> Istio
  -> Prometheus
  -> 晋级或中止
```

本地 `kind` 集群只是本次验证使用的 Kubernetes 参考环境。它是外部提供的基础设施，
不是应用架构依赖。仓库不会创建、检测或管理 kind 集群，也不编码任何 kind 专属的应用逻辑。

已验证的最终环境如下：

- Kubernetes context：`kind-argo-canary-local`
- kind 集群：`argo-canary-local`
- 节点：`Ready`
- Argo CD：`Synced / Healthy`
- Argo Rollouts：`Healthy`
- 应用 HTTP 路径：`200`
