# 外部 Kubernetes 集群客户端准备

这份笔记只说明客户端工具和连接前提，不创建或管理 Kubernetes 集群，也不绑定 Ubuntu、Azure 或某一种 Kubernetes 发行版。

## 工具

请按照操作系统和组织策略安装官方版本的：

- `kubectl`
- Helm 3+
- Docker（仅用于本地镜像构建或 Docker + kind 参考环境）

验证客户端：

```shell
kubectl version --client
helm version
docker version
```

## 外部集群连接

集群管理员或云平台负责提供 kubeconfig。配置完成后，从当前 context 开始验证：

```shell
kubectl config current-context
kubectl cluster-info
kubectl get nodes
```

如果这些命令失败，应修复外部集群访问或 kubeconfig；本仓库不会自动创建集群、修改 context、安装控制器或选择云平台。

## 项目能力预检

```shell
kubectl api-resources --api-group argoproj.io
kubectl api-resources --api-group networking.istio.io
kubectl api-resources --api-group monitoring.coreos.com
kubectl get crd applications.argoproj.io rollouts.argoproj.io \
  virtualservices.networking.istio.io gateways.networking.istio.io \
  servicemonitors.monitoring.coreos.com
```

继续使用主 [README](README.md) 的 capability-based prerequisite validation 和 GitOps 流程。
