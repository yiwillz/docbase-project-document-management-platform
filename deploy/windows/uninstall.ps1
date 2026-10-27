<#
docbase 卸载：停止并删除三个 Windows 服务和防火墙规则。

  .\uninstall.ps1                 # 只删服务；程序、配置、日志、数据库、文件、备份都留着（可以再 install.ps1 装回来）
  .\uninstall.ps1 -RemoveProgram  # 另外删掉安装目录里的程序和虚拟环境（配置、日志、证书仍保留）

数据库、文件存储目录、备份目录一律不删，需要的话请自己手动处理。
#>
param(
    [string]$InstallDir = 'C:\docbase',
    [switch]$RemoveProgram
)

. (Join-Path $PSScriptRoot 'common.ps1')
trap { Show-Failure $_; exit 1 }
if (-not (Test-Admin)) { Stop-WithError '请用"以管理员身份运行"的 PowerShell 执行' }

Write-Step '停止并删除服务'
Stop-DocbaseServices
foreach ($id in $Services) {
    $exe = Join-Path $InstallDir "services\$id.exe"
    if ((Get-Service $id -ErrorAction SilentlyContinue) -and (Test-Path $exe)) {
        & $exe uninstall | Out-Null
        Write-Ok "已删除服务 $id"
    }
}
Get-NetFirewallRule -DisplayName 'docbase HTTPS' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Ok '已删除防火墙规则'

if ($RemoveProgram) {
    Write-Step '删除程序文件'
    foreach ($d in @('app', 'app.previous', 'venv', 'services', 'proxy')) {
        $p = Join-Path $InstallDir $d
        if (Test-Path $p) { Remove-Item $p -Recurse -Force; Write-Ok "已删除 $p" }
    }
}
Write-Host ''
Write-Host '卸载完成。数据库、文件存储、备份没有动。' -ForegroundColor Green
