# 最终完整版 **（已补充 az login 完整步骤）**
全部复制粘贴就能用，一步不漏！

---

## 【第一步】安装所有工具（curl + kubectl + az cli + helm）
```bash
# 1. 安装依赖和 curl
sudo apt update && sudo apt install -y apt-transport-https ca-certificates curl

# 2. 安装 kubectl（官方源）
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/trusted.gpg.d/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo apt update
sudo apt install -y kubectl

# 3. 安装 Azure CLI
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# 4. 安装 Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

---

## 【第二步】验证安装成功（可选，但建议跑）
```bash
kubectl version --client
az version
helm version
```

---

# 【第三步】AZ LOGIN 完整步骤（纯命令行，无图形）
## 直接运行这条命令：
```bash
az login --use-device-code
```

## 它会输出：
```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code XXXXXXX to authenticate.
```

## 登录步骤：
1. **打开你 Windows 的浏览器**
2. 访问：**https://microsoft.com/devicelogin**
3. 输入 Ubuntu 里显示的 **代码**
4. 登录你的 **Azure 学生账号**
5. 登录成功后，浏览器显示「已登录」
6. **回到 Ubuntu 终端，自动登录完成！**

---

## 【第四步】选择订阅（学生订阅）
登录成功后，会显示你的订阅列表：
```
[1] * Azure for Students
```
直接输入 **1** 回车即可。

---

# ✅ 全部搞定！
现在你的 Ubuntu 环境：
- ✅ 装好 `kubectl`
- ✅ 装好 `az cli`
- ✅ 装好 `helm`
- ✅ 装好 `curl`
- ✅ **已登录 Azure**
- ✅ **随时可以连接 AKS**

---

## 你接下来只要运行这一条就能连集群：
```bash
az aks get-credentials --resource-group mario-aks-auckland-meetup-2026-demo-1 --name mario-aks-auckland-demo-1
```

需要我把 **连接 AKS + 安装 Istio + Argo Rollouts** 的全套流程也整理好吗？