# Ask the trading agent from PowerShell.
#   .\ask.ps1 "BTC long, 20x, entry 98,400, how does it look?"          quick check (~30s first run)
#   .\ask.ps1 -Agent "BTC long, 20x, entry 98,400, how does it look?"   full Claude Code agent (minutes)
param([switch]$Agent, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Text)
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$script = Join-Path $PSScriptRoot "app\ask.py"
if ($Agent) { & $py $script --agent ($Text -join " ") } else { & $py $script ($Text -join " ") }
