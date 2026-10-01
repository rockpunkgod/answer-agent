param([int]$Port = 8765, [string]$Db = "data/demo-ui.db")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
python -m helpdesk.demo_server --port $Port --db $Db
