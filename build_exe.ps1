$ErrorActionPreference = "Stop"

$python = ".\\.venv\\Scripts\\python.exe"
if (!(Test-Path $python)) {
    $python = "python"
}

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt
& $python -m pip install pyinstaller

$addData = @(
    "scripts;scripts",
    "styles;styles",
    "assets;assets",
    "uploads;uploads",
    "data;data",
    "Advertencia;Advertencia",
    "desligados;desligados"
)

$rootFiles = Get-ChildItem -File -Path . | Where-Object {
    $_.Extension -in ".html", ".json", ".png", ".ico", ".svg", ".txt"
}

foreach ($file in $rootFiles) {
    $addData += "$($file.Name);."
}

$appName = $env:APP_NAME
if ([string]::IsNullOrWhiteSpace($appName)) {
    $appName = "UltraRH"
}

$pyiArgs = @(
    "--onefile",
    "--name", $appName,
    "--noconsole",
    "app_launcher.py"
)

foreach ($entry in $addData) {
    $pyiArgs += "--add-data"
    $pyiArgs += $entry
}

& $python -m PyInstaller @pyiArgs

Write-Host "Executavel gerado em dist\\$appName.exe"
