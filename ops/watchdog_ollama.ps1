param(
  [string]$OllamaExe = 'C:\Users\user\AppData\Local\Programs\Ollama\ollama.exe',
  [string]$ModelsDir = 'D:\Ollama\models',
  [string]$LogFile = 'C:\Users\user\Desktop\codex\ops\watchdog_ollama.log'
)

$ErrorActionPreference = 'Stop'
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Write-Log([string]$message) {
  Add-Content -LiteralPath $LogFile -Value "[$timestamp] $message" -Encoding UTF8
}

$env:OLLAMA_MODELS = $ModelsDir
$env:OLLAMA_KEEP_ALIVE = '30m'
$env:OLLAMA_MAX_LOADED_MODELS = '2'
$env:OLLAMA_NUM_PARALLEL = '1'

if (-not (Test-Path $OllamaExe)) {
  Write-Log "ERROR: missing ollama executable at $OllamaExe"
  exit 1
}

try {
  & $OllamaExe list | Out-Null
  Write-Log 'OK: health-check passed'
  exit 0
} catch {
  Write-Log "WARN: health-check failed ($($_.Exception.Message)); restarting service"
}

Get-Process -Name ollama -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Start-Process -FilePath $OllamaExe -ArgumentList 'serve' -WindowStyle Hidden
Start-Sleep -Seconds 3

try {
  & $OllamaExe list | Out-Null
  Write-Log 'OK: service restarted successfully'
  exit 0
} catch {
  Write-Log "ERROR: restart failed ($($_.Exception.Message))"
  exit 1
}
