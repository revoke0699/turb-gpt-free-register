# Turb GPT Free Register WebUI 管理脚本（Windows）
#
# 用法：
#   .\webui.ps1 start      启动 WebUI
#   .\webui.ps1 stop       关闭 WebUI
#   .\webui.ps1 restart    重启 WebUI
#   .\webui.ps1 status     查看状态
#   .\webui.ps1 logs       实时查看日志
#
# 也可双击或运行 webui.bat（无参数时默认 start）。
#
# 可选环境变量：
#   HOST=127.0.0.1
#   PORT=5000
#   OPEN_BROWSER=1
#   VERBOSE=1
#   AUTH_CODE=xxx
#   EXTRA_ARGS="..."

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = ""
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

$RunDir = Join-Path $RootDir "run"
$LogDir = Join-Path $RootDir "logs"
$PidFile = Join-Path $RunDir "webui.pid"
$LogFile = Join-Path $LogDir "webui.log"

function Get-EnvOrDefault([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

function Test-Flag([string]$Value) {
    return $Value -in @("1", "true", "True", "TRUE", "yes", "YES", "on", "ON")
}

$HostAddr = Get-EnvOrDefault "HOST" "127.0.0.1"
$Port = Get-EnvOrDefault "PORT" "5000"
$OpenBrowser = Get-EnvOrDefault "OPEN_BROWSER" "0"
$VerboseLog = Get-EnvOrDefault "VERBOSE" "0"
$AuthCode = Get-EnvOrDefault "AUTH_CODE" ""
$ExtraArgs = Get-EnvOrDefault "EXTRA_ARGS" ""

New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null

function Show-Usage {
    @"
用法：webui.bat <command>   或   .\webui.ps1 <command>

commands:
  start      启动 WebUI
  stop       关闭 WebUI
  restart    重启 WebUI
  status     查看运行状态
  logs       实时查看日志

环境变量：
  HOST=127.0.0.1 PORT=5000 OPEN_BROWSER=1 VERBOSE=1 AUTH_CODE=xxx EXTRA_ARGS="..."

示例：
  webui.bat start
  set PORT=8000 && set OPEN_BROWSER=1 && webui.bat start
  set HOST=0.0.0.0 && set PORT=5000 && webui.bat restart
  set AUTH_CODE=你的授权码 && webui.bat start
"@ | Write-Host
}

function Test-ProcessRunning([string]$ProcessIdValue) {
    if ([string]::IsNullOrWhiteSpace($ProcessIdValue)) { return $false }
    $id = 0
    if (-not [int]::TryParse($ProcessIdValue, [ref]$id)) { return $false }
    if ($id -le 0) { return $false }
    return [bool](Get-Process -Id $id -ErrorAction SilentlyContinue)
}

function Read-PidFile {
    if (-not (Test-Path -LiteralPath $PidFile)) { return "" }
    $raw = (Get-Content -LiteralPath $PidFile -TotalCount 1 -ErrorAction SilentlyContinue)
    if ($null -eq $raw) { return "" }
    return $raw.ToString().Trim()
}

function Get-Python {
    $venvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return @{ FileName = $venvPython; PrefixArgs = @() }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source) {
        return @{ FileName = $python.Source; PrefixArgs = @() }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py -and $py.Source) {
        return @{ FileName = $py.Source; PrefixArgs = @("-3") }
    }

    throw "未找到 Python：请先创建 .venv 或安装 python / py launcher"
}

function Get-WebUiPids {
    $ids = New-Object System.Collections.Generic.List[int]
    $fromFile = Read-PidFile
    if (Test-ProcessRunning $fromFile) {
        $ids.Add([int]$fromFile) | Out-Null
    }

    $portPattern = "--port[ =]$([regex]::Escape($Port))(\s|$)"
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        $cmd = $_.CommandLine
        if ([string]::IsNullOrWhiteSpace($cmd)) { return }
        if ($cmd -notmatch "web\.py") { return }
        if ($cmd -notmatch $portPattern) { return }
        if (-not $ids.Contains([int]$_.ProcessId)) {
            $ids.Add([int]$_.ProcessId) | Out-Null
        }
    }

    return @($ids)
}

function Get-StartArgumentList {
    $python = Get-Python
    $argsList = New-Object System.Collections.Generic.List[string]
    foreach ($item in $python.PrefixArgs) { $argsList.Add($item) | Out-Null }
    $argsList.Add("web.py") | Out-Null
    $argsList.Add("--host") | Out-Null
    $argsList.Add($HostAddr) | Out-Null
    $argsList.Add("--port") | Out-Null
    $argsList.Add($Port) | Out-Null
    if (Test-Flag $OpenBrowser) {
        $argsList.Add("--open-browser") | Out-Null
    }
    if (Test-Flag $VerboseLog) {
        $argsList.Add("--verbose") | Out-Null
    }
    if (-not [string]::IsNullOrWhiteSpace($AuthCode)) {
        $argsList.Add("--auth-code") | Out-Null
        $argsList.Add($AuthCode) | Out-Null
    }
    if (-not [string]::IsNullOrWhiteSpace($ExtraArgs)) {
        foreach ($part in ($ExtraArgs -split "\s+")) {
            if (-not [string]::IsNullOrWhiteSpace($part)) {
                $argsList.Add($part) | Out-Null
            }
        }
    }
    return @{ FileName = $python.FileName; Arguments = @($argsList) }
}

function Quote-CmdArg([string]$Value) {
    return '"' + ($Value -replace '"', '""') + '"'
}

function Start-WebUi {
    $oldPid = Read-PidFile
    if (Test-ProcessRunning $oldPid) {
        Write-Host "WebUI 已在运行：PID=$oldPid，地址：http://${HostAddr}:${Port}"
        return
    }
    if (Test-Path -LiteralPath $PidFile) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }

    $start = Get-StartArgumentList
    $quoted = @($start.FileName) + $start.Arguments | ForEach-Object { Quote-CmdArg $_ }
    $cmdline = ($quoted -join " ") + " >> $(Quote-CmdArg $LogFile) 2>&1"

    Write-Host "启动 WebUI：http://${HostAddr}:${Port}"
    Write-Host "日志文件：$LogFile"

    $wrapper = Start-Process -FilePath $env:ComSpec `
        -WorkingDirectory $RootDir `
        -ArgumentList @("/d", "/s", "/c", $cmdline) `
        -WindowStyle Hidden `
        -PassThru

    Start-Sleep -Seconds 1

    $pythonPid = $null
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($wrapper.Id)" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        if ($child.Name -match "python|py") {
            $pythonPid = [int]$child.ProcessId
            break
        }
    }
    if (-not $pythonPid) {
        $running = @(Get-WebUiPids)
        if ($running.Count -gt 0) {
            $pythonPid = [int]$running[0]
        }
    }
    if (-not $pythonPid) {
        $pythonPid = [int]$wrapper.Id
    }

    Set-Content -LiteralPath $PidFile -Value $pythonPid -Encoding ascii

    if (Test-ProcessRunning $pythonPid) {
        Write-Host "启动成功：PID=$pythonPid"
    } else {
        if (Test-Path -LiteralPath $PidFile) {
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        }
        Write-Host "启动失败，请查看日志：$LogFile" -ForegroundColor Red
        exit 1
    }
}

function Stop-WebUi {
    $pids = @(Get-WebUiPids)
    if ($pids.Count -eq 0) {
        if (Test-Path -LiteralPath $PidFile) {
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        }
        Write-Host "WebUI 未运行"
        return
    }

    Write-Host ("正在关闭 WebUI：PID=" + ($pids -join " "))
    foreach ($id in $pids) {
        & taskkill /PID $id /T > $null 2>&1
    }

    for ($i = 0; $i -lt 15; $i++) {
        $alive = $false
        foreach ($id in $pids) {
            if (Test-ProcessRunning $id) {
                $alive = $true
                break
            }
        }
        if (-not $alive) { break }
        Start-Sleep -Seconds 1
    }

    foreach ($id in $pids) {
        if (Test-ProcessRunning $id) {
            Write-Host "进程未退出，强制结束：PID=$id"
            & taskkill /PID $id /T /F > $null 2>&1
        }
    }

    if (Test-Path -LiteralPath $PidFile) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Host "已关闭 WebUI"
}

function Show-Status {
    $pids = @(Get-WebUiPids)
    if ($pids.Count -eq 0) {
        Write-Host "WebUI 未运行"
        exit 1
    }
    Write-Host ("WebUI 运行中：PID=" + ($pids -join " "))
    Write-Host "地址：http://${HostAddr}:${Port}"
    Write-Host "日志：$LogFile"
}

function Show-Logs {
    if (-not (Test-Path -LiteralPath $LogFile)) {
        New-Item -ItemType File -Path $LogFile -Force | Out-Null
    }
    Get-Content -LiteralPath $LogFile -Tail 120 -Wait
}

switch -Regex ($Command) {
    "^(start)$" { Start-WebUi }
    "^(stop)$" { Stop-WebUi }
    "^(restart)$" { Stop-WebUi; Start-Sleep -Seconds 1; Start-WebUi }
    "^(status)$" { Show-Status }
    "^(logs|log)$" { Show-Logs }
    "^$|^(-h|--help|help)$" { Show-Usage }
    default {
        Write-Host "未知命令：$Command" -ForegroundColor Red
        Show-Usage
        exit 2
    }
}
