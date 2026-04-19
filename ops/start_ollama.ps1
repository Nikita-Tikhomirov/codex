param(
  [string]$OllamaExe = 'C:\Users\user\AppData\Local\Programs\Ollama\ollama.exe',
  [string]$ModelsDir = 'D:\Ollama\models'
)

$ErrorActionPreference = 'Stop'
$env:OLLAMA_MODELS = $ModelsDir
$env:OLLAMA_KEEP_ALIVE = '30m'
$env:OLLAMA_MAX_LOADED_MODELS = '2'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_CONTEXT_LENGTH = '4096'

if (-not (Test-Path $OllamaExe)) {
  throw "Ollama executable not found: $OllamaExe"
}

# If serve is already running, do nothing.
$running = Get-Process -Name ollama -ErrorAction SilentlyContinue
if ($running) {
  Write-Output 'ollama serve already running'
  exit 0
}

Start-Process -FilePath $OllamaExe -ArgumentList 'serve' -WindowStyle Hidden
Start-Sleep -Seconds 2

try {
  & $OllamaExe list | Out-Null
  Write-Output 'ollama serve started'
} catch {
  throw "Failed to start ollama serve: $($_.Exception.Message)"
}
