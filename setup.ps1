$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtimeDir = Join-Path $PSScriptRoot '.runtime'
$pythonPath = Join-Path $runtimeDir 'python/python.exe'
New-Item -ItemType Directory -Force $runtimeDir | Out-Null
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host 'Downloading project-local Python 3.12.10 from python.org...'
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile (Join-Path $runtimeDir 'python.zip')
    Expand-Archive -LiteralPath (Join-Path $runtimeDir 'python.zip') -DestinationPath (Join-Path $runtimeDir 'python') -Force
}
@('python312.zip','.','Lib/site-packages','..\..','import site') | Set-Content -Encoding ascii (Join-Path $runtimeDir 'python/python312._pth')
if (-not (Test-Path -LiteralPath (Join-Path $runtimeDir 'python/Lib/site-packages/pip'))) {
    Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile (Join-Path $runtimeDir 'get-pip.py')
    & $pythonPath (Join-Path $runtimeDir 'get-pip.py')
    if ($LASTEXITCODE -ne 0) { throw 'pip setup failed.' }
}
& $pythonPath -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your internet connection and try again.' }
Write-Host 'Setup complete. Double-click start.bat, then open http://127.0.0.1:5000'


