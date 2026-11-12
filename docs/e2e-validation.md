# 实验与运维指南

使用真实 kind、Istio 网关、Prometheus 和 Argo CD。实验会提交并推送 lab values，请在专用分支执行，并先运行 bootstrap.ps1 -DeployApplication -GitOpsRef <该分支>。不要在 production Application 上注入故障。

## 流量和所有权

请求经过 127.0.0.1:18080，Host 为 demo-app.local。直接转发 Service / Pod 只能诊断，不能证明 Istio 加权路由。

Rollouts 管理 Stable/Canary Service 的 Pod hash 和 VirtualService 权重。Argo CD 忽略这些字段的差异并使用 ApplyOutOfSyncOnly=true，避免普通应用更新重写路由。Service 和 Rollout 使用 server-side apply，保留其他控制器拥有的选择器 / scale 字段。不要使用会把整个 HTTP 数组从 live 复制回 desired 的 RespectIgnoreDifferences 补丁冻结非权重字段。

Pod 模板不包含 Chart version/appVersion。分析阈值、入口域名不创建新 Pod 修订；digest、应用 release 和故障环境变量会。启用 HPA 时 chart 不声明 replicas；关闭后恢复 Git 副本数。

## 三态分析

ServiceMonitor 把被抓取 Pod 的模板 hash 写成 rollout_hash。AnalysisRun 传入 Latest hash，查询同时限定命名空间及 reporter=destination。Stable 和旧候选的数据不会与新候选混合。

| 指标 | 默认条件 |
| --- | --- |
| 最小样本 | 最近 1 分钟 increase 估算候选请求数 ≥ 50 |
| HTTP 成功率 | 候选非 5xx / 全部候选请求 ≥ 99% |
| P95 | Istio request duration histogram < 500 ms |

成功率及 P95 查询都用最小样本量过滤。全部为 500 时，成功请求分子显式补零；空数组、NaN、Infinity 和样本不足均不会自动成功或失败。

默认每 10 秒测量；连续 2 次成功通过；超过 1 次失败终止；超过 5 次 Inconclusive 结束并暂停；总测量次数 8，另有 provider 错误上限。预热与 30 秒观察先于分析。分析预算有界，结束后的暂停需人工处理。

更大的样本、窗口和测量次数可降低偶然抖动影响，也会延长检测及故障暴露。直方图 P95 是桶内估算：800 ms 注入可能得到接近 975 ms 的结果，应同时查看客户端延迟。

```powershell
kubectl --context kind-argo-canary-local -n demo get rollout,analysisrun
kubectl --context kind-argo-canary-local -n demo get virtualservice demo-app -o yaml
kubectl --context kind-argo-canary-local -n demo get service demo-app-stable demo-app-canary -o yaml
kubectl --context kind-argo-canary-local -n demo get endpointslices
```

在 Prometheus 中用实际候选 hash 替换占位符：

```promql
sum(increase(istio_requests_total{
  reporter="destination", destination_workload_namespace="demo",
  rollout_hash="<candidate-hash>"
}[1m]))

histogram_quantile(0.95, sum by(le)(rate(
  istio_request_duration_milliseconds_bucket{
    reporter="destination", destination_workload_namespace="demo",
    rollout_hash="<candidate-hash>"
  }[1m]
)))
```

## 实验

在其他终端保持 Prometheus 的 19090 端口转发，然后运行：

```powershell
$env:PATH = "$PWD\.local\bin;$env:PATH"
python scripts/lab.py healthy
python scripts/lab.py manual
python scripts/lab.py http500
python scripts/lab.py latency
python scripts/lab.py no-data
python scripts/lab.py configuration
python scripts/lab.py bluegreen
python scripts/lab.py hpa
```

| 实验 | 验证内容 |
| --- | --- |
| EXP-01 | CI 已批准 digest，10%/50% 实测比例、两个分析、端点 / 代理、晋级及旧副本缩容 |
| EXP-02 | 分析成功后的无限 Pause；观察不自动推进；Abort、Retry、逐级 Promote |
| EXP-03 | 真实 500、候选成功率失败、Abort、实际入口恢复及 Git revert |
| EXP-04 | 约 800 ms 延迟且 HTTP 200；只有 P95 失败，恢复及 Stable 配置隔离 |
| EXP-05 | 无数据预算耗尽为 Inconclusive 并暂停；恢复流量后 Abort + Retry 重新分析 |
| EXP-06 | EXP-02 的 50% 门禁处，同一应用临时 L4 Service 的新连接 / Keep-Alive 与 Istio L7 单连接对比 |
| EXP-07 | Abort 后 Synced / Degraded；Git revert 后 Healthy；重建 Stable Pod 检查配置 |
| EXP-08 | 真实 production Environment 等待、本人审批、candidate artifact、GitOps 提交及部署 digest |
| EXP-09 | 非 Pod values / Chart 元数据不创建修订，Pod 变化创建修订，所有权和回退 |
| EXP-10 | 临时 Argo CD Application 使用同一 chart；Active/Preview、预分析、受控晋级、故障 Preview 和恢复 |
| EXP-11 | 真实 CPU 指标、Rollout scale 扩容，移除 HPA 并恢复 Git 副本数 |

临时连接测试 Service / 客户端及 Blue/Green 命名空间在结束时删除。脚本只允许 kind-argo-canary-local。部分输出不能当作完整 PASS；失败时先检查 Git 与运行状态，再修复重试。

## 恢复

retry rollout 重试 Abort，不会重做仍暂停的 Inconclusive 分析；此时 Promote 会跳过该分析。要重新评估，先恢复流量，Abort 并等 Degraded / Stable 路由，再 Retry 创建新 AnalysisRun。不要用 promote --full 替代分析。

Abort 不改 Git。错误配置需要：

```powershell
git revert <只包含错误实验配置的提交>
git push
kubectl --context kind-argo-canary-local -n argocd annotate application demo-app argocd.argoproj.io/refresh=hard --overwrite
```

配置权重与 Envoy 数据面收敛要分别检查。幂等 GET 的连接失败、reset 和 503 使用有限重试；注入的 500 不重试。回退验证覆盖镜像与 Pod 环境变量，不覆盖数据库迁移、外部副作用或共享状态。

## GitHub 审批

继续使用已有 DOCKERHUB_TOKEN 和 GH_PAT；构建 Job 无 Git 写权限。production Job 获批后才取得原生 token 的 contents:write；GH_PAT 检查 Environment reviewer 规则。仓库 Secret 不能代替 Required reviewers。关闭管理员绕过并限制可部署分支；代理不能代替本人审批。

参考：[Argo 分析](https://argoproj.github.io/argo-rollouts/features/analysis/)、[Istio 指标与单位](https://istio.io/latest/docs/reference/config/metrics/)、[GitHub Environment](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)。
