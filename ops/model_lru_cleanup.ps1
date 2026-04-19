param(
  [string]$OllamaExe = 'C:\Users\user\AppData\Local\Programs\Ollama\ollama.exe',
  [double]$MinFreeGB = 20,
  [string[]]$PinnedModels = @('qwen2.5-coder:7b','qwen2.5-coder:7b-instruct-q6_K','qwen3:8b','deepseek-r1:8b','bge-m3')
)

$ErrorActionPreference = 'Stop'
$drive = Get-PSDrive -Name D
$freeGB = [math]::Round($drive.Free / 1GB, 2)
if ($freeGB -ge $MinFreeGB) {
  Write-Output "Skip cleanup: free space ${freeGB}GB >= threshold ${MinFreeGB}GB"
  exit 0
}

$lines = & $OllamaExe list | Select-Object -Skip 1 | Where-Object { $_.Trim() -ne '' }
if (-not $lines) { exit 0 }

$records = foreach ($line in $lines) {
  $parts = $line -split '\s{2,}'
  if ($parts.Count -ge 4) {
    [pscustomobject]@{
      Name = $parts[0].Trim()
      Modified = $parts[3].Trim()
    }
  }
}

$toDelete = $records | Where-Object { $PinnedModels -notcontains $_.Name }
foreach ($model in $toDelete) {
  Write-Output "Removing model: $($model.Name)"
  & $OllamaExe rm $model.Name | Out-Null
}
