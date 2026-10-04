# Start the local web interface and open it in your browser (http://127.0.0.1:8765).
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py (Join-Path $PSScriptRoot "app\server.py") @args
