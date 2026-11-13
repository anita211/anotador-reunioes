# Instala o Anotador de Reunioes: ambiente Python, configuracao e inicio automatico com o Windows.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
$venvPyw = Join-Path $root '.venv\Scripts\pythonw.exe'

if (-not (Test-Path $venvPy)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv --python 3.12 (Join-Path $root '.venv')
    } else {
        py -3.12 -m venv (Join-Path $root '.venv')
    }
}
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python $venvPy -r (Join-Path $root 'requirements.txt')
} else {
    & $venvPy -m pip install -r (Join-Path $root 'requirements.txt')
}

$workDir = Join-Path $env:LOCALAPPDATA 'AnotadorReunioes'
New-Item -ItemType Directory -Force $workDir | Out-Null
$cfg = Join-Path $workDir 'config.json'
if (-not (Test-Path $cfg)) { Copy-Item (Join-Path $root 'config.example.json') $cfg }

$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'Anotador de Reunioes.lnk'
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $venvPyw
$lnk.Arguments = '-m anotador'
$lnk.WorkingDirectory = $root
$lnk.Description = 'Anotador de Reunioes'
$lnk.Save()
Write-Host "Atalho de inicializacao criado: $lnkPath"

Start-Process -FilePath $venvPyw -ArgumentList '-m anotador' -WorkingDirectory $root -WindowStyle Hidden
Write-Host 'Anotador iniciado (icone perto do relogio).'
