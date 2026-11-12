param([string]$ClusterName = 'argo-canary-local')
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$dockerBin = 'C:\Program Files\Docker\Docker\resources\bin'
if (-not (Get-Command docker -ErrorAction SilentlyContinue) -and (Test-Path "$dockerBin\docker.exe")) {
    $env:PATH = "$dockerBin;$env:PATH"
}
function Run([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE)" }
}
foreach ($tool in @('docker', 'kind', 'kubectl', 'helm', 'python')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Missing tool: $tool" }
}
Run docker @('info', '--format', 'Docker {{.ServerVersion}} / {{.OSType}} / {{.MemTotal}} bytes')
$context = "kind-$ClusterName"
$previous = & kubectl config current-context
$clusters = & kind get clusters
if ($LASTEXITCODE -ne 0) { throw 'Cannot list kind clusters' }
try {
    if ($clusters -notcontains $ClusterName) {
        Run kind @('create', 'cluster', '--name', $ClusterName, '--config', "$repo/infra/local/kind.yaml", '--wait', '180s')
    } else {
        Run kind @('export', 'kubeconfig', '--name', $ClusterName)
    }
} finally {
    if ($previous -and $previous -ne $context) { & kubectl config use-context $previous | Out-Null }
}
Run kubectl @('--context', $context, 'wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=180s')
foreach ($entry in @(
    @('argo', 'https://argoproj.github.io/argo-helm'),
    @('istio', 'https://istio-release.storage.googleapis.com/charts'),
    @('prometheus-community', 'https://prometheus-community.github.io/helm-charts'),
    @('metrics-server', 'https://kubernetes-sigs.github.io/metrics-server/')
)) { Run helm @('repo', 'add', $entry[0], $entry[1], '--force-update') }
Run helm @('repo', 'update')
New-Item -ItemType Directory -Path "$repo/.local" -Force | Out-Null
Run python @('-c', 'import pathlib,sys,yaml; d=yaml.safe_load(pathlib.Path(sys.argv[1]).read_text()); p=pathlib.Path(sys.argv[2]); [(p/(k+".yaml")).write_text(yaml.safe_dump(v)) for k,v in d.items()]', "$repo/infra/local/platform.yaml", "$repo/.local")
function Install([string]$Release, [string]$Chart, [string]$Namespace, [string]$Version, [string[]]$Extra = @()) {
    Run helm (@('upgrade', '--install', $Release, $Chart, '--kube-context', $context, '--namespace', $Namespace, '--create-namespace', '--version', $Version, '--wait', '--timeout', '8m') + $Extra)
}
Install istio-base istio/base istio-system 1.30.8
Install istiod istio/istiod istio-system 1.30.8 @('--set', 'pilot.resources.requests.cpu=100m', '--set', 'pilot.resources.requests.memory=128Mi')
Install istio-ingressgateway istio/gateway istio-system 1.30.8 @('--set', 'service.type=NodePort', '--set', 'service.ports[0].name=status-port', '--set', 'service.ports[0].port=15021', '--set', 'service.ports[0].targetPort=15021', '--set', 'service.ports[1].name=http2', '--set', 'service.ports[1].port=80', '--set', 'service.ports[1].targetPort=8080', '--set', 'service.ports[1].nodePort=32080', '--set', 'resources.requests.cpu=100m', '--set', 'resources.requests.memory=64Mi')
Install argo-rollouts argo/argo-rollouts argo-rollouts 2.43.12
Install argocd argo/argo-cd argocd 10.18.0 @('-f', "$repo/.local/argocd.yaml")
Install prometheus prometheus-community/kube-prometheus-stack monitoring 93.2.2 @('-f', "$repo/.local/prometheus.yaml")
Install metrics-server metrics-server/metrics-server kube-system 3.13.0 @('--set', 'args[0]=--kubelet-insecure-tls')
& kubectl --context $context create namespace demo --dry-run=client -o yaml | & kubectl --context $context apply -f -
if ($LASTEXITCODE -ne 0) { throw 'Namespace creation failed' }
Run kubectl @('--context', $context, 'label', 'namespace', 'demo', 'istio-injection=enabled', '--overwrite')
Write-Output "Ready. Explicit context: $context. Existing default context preserved."
