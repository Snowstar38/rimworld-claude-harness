$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridge = Join-Path $root 'bridge'
$profile = Join-Path $root 'profile'
$gabsDir = Join-Path $bridge 'gabs'
$modDir = Join-Path ${env:ProgramFiles(x86)} 'Steam\steamapps\common\RimWorld\Mods\RimBridgeServer'
$managed = Join-Path ${env:ProgramFiles(x86)} 'Steam\steamapps\common\RimWorld\RimWorldWin64_Data\Managed'

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw 'Claude Code is required and must be on PATH.' }
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw '.NET SDK is required and must be on PATH.' }
if (-not (Test-Path $managed)) { throw 'RimWorld was not found in the default Steam library. See README.md for custom paths.' }

New-Item -ItemType Directory -Path $bridge,$gabsDir,$modDir,(Join-Path $bridge 'gabs-config') -Force | Out-Null

Write-Host 'Downloading Pardeike GABS (not redistributed by this repository)...'
$gabsRelease = Invoke-RestMethod 'https://api.github.com/repos/pardeike/GABS/releases/latest'
$gabsAsset = $gabsRelease.assets | Where-Object name -Match 'windows-amd64\.zip$' | Select-Object -First 1
if (-not $gabsAsset) { throw 'No Windows x64 GABS release asset was found.' }
$gabsZip = Join-Path $env:TEMP 'rimworld-harness-gabs.zip'
Invoke-WebRequest $gabsAsset.browser_download_url -OutFile $gabsZip
$gabsExtract = Join-Path $env:TEMP 'rimworld-harness-gabs'
New-Item -ItemType Directory -Path $gabsExtract -Force | Out-Null
Expand-Archive $gabsZip -DestinationPath $gabsExtract -Force
$gabsExe = Get-ChildItem $gabsExtract -Filter gabs.exe -Recurse | Select-Object -First 1
Copy-Item $gabsExe.FullName (Join-Path $gabsDir 'gabs.exe') -Force

Write-Host 'Downloading Pardeike RimBridgeServer (not redistributed by this repository)...'
$rimRelease = Invoke-RestMethod 'https://api.github.com/repos/pardeike/RimBridgeServer/releases/latest'
$rimAsset = $rimRelease.assets | Where-Object name -Match '\.zip$' | Select-Object -First 1
if (-not $rimAsset) { throw 'No RimBridgeServer release zip was found.' }
$rimZip = Join-Path $env:TEMP 'rimworld-harness-rimbridge.zip'
Invoke-WebRequest $rimAsset.browser_download_url -OutFile $rimZip
$rimExtract = Join-Path $env:TEMP 'rimworld-harness-rimbridge'
New-Item -ItemType Directory -Path $rimExtract -Force | Out-Null
Expand-Archive $rimZip -DestinationPath $rimExtract -Force
$rimRoot = Get-ChildItem $rimExtract -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'About\About.xml') } | Select-Object -First 1
if (-not $rimRoot) { throw 'The RimBridgeServer mod folder was not found after extraction.' }
Copy-Item (Join-Path $rimRoot.FullName '*') $modDir -Recurse -Force

$sdk = Get-ChildItem $modDir -Filter RimBridgeServer.Sdk.dll -Recurse | Select-Object -First 1
if (-not $sdk) { throw 'RimBridgeServer.Sdk.dll was not found after extraction.' }
Write-Host 'Building the companion tools...'
dotnet build (Join-Path $root 'companion\src\HomeBridge.BridgeTools.csproj') -c Release "/p:RimBridgeSdkDir=$($sdk.Directory.FullName)" "/p:RimWorldManagedDir=$managed"
if ($LASTEXITCODE) { throw 'Companion build failed.' }
$toolDest = Join-Path (Split-Path $modDir -Parent) 'BridgeTools\HomeBridge'
New-Item -ItemType Directory -Path $toolDest -Force | Out-Null
Copy-Item (Join-Path $root 'companion\artifacts\BridgeTools\HomeBridge\HomeBridge.BridgeTools.dll') $toolDest -Force

$config = @{
  version = '1.0'
  stripOutputSchema = $true
  toolNormalization = @{ enableOpenAINormalization = $true; maxToolNameLength = 64; preserveOriginalName = $true }
  games = @{ rimworld = @{ id='rimworld'; name='RimWorld (agent profile)'; launchMode='SteamManaged'; target='294100'; args=@("-savedatafolder=$profile"); stopProcessName='RimWorldWin64' } }
} | ConvertTo-Json -Depth 8
$config | Set-Content (Join-Path $bridge 'gabs-config\config.json') -Encoding UTF8

Write-Host 'Registering GABS with Claude Code for this project...'
Push-Location $root
try { claude mcp remove gabs -s local 2>$null | Out-Null } catch {}
claude mcp add -s local gabs -- (Join-Path $gabsDir 'gabs.exe') server --configDir (Join-Path $bridge 'gabs-config')
Pop-Location
if ($LASTEXITCODE) { throw 'Claude Code MCP registration failed.' }
Write-Host 'Installed from https://github.com/pardeike/RimBridgeServer and https://github.com/pardeike/GABS'
