$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$setupDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($env:ez3D_STUDIO_SETUP_VISIBLE -ne '1') {
  # IExpress may hide the initial process and remove its extraction directory
  # as soon as that process exits. Copy the small setup payload to a stable
  # per-user location before reopening it in a visible PowerShell window.
  $env:ez3D_STUDIO_SETUP_VISIBLE = '1'
  try {
    $stableSetupDir = Join-Path $env:LOCALAPPDATA 'ez3D\Installer'
    New-Item -ItemType Directory -Force -Path $stableSetupDir | Out-Null
    foreach ($file in @('Install-ez3D.ps1','ez3DPayload.zip','Start-ez3D.bat','windows-requirements.txt')) {
      Copy-Item -LiteralPath (Join-Path $setupDir $file) -Destination (Join-Path $stableSetupDir $file) -Force
    }
    $scriptPath = Join-Path $stableSetupDir 'Install-ez3D.ps1'
    $argumentList = @('-NoLogo','-NoProfile','-ExecutionPolicy','RemoteSigned','-WindowStyle','Normal','-File',('"{0}"' -f $scriptPath))
    Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $argumentList -WorkingDirectory $stableSetupDir -WindowStyle Normal | Out-Null
  } catch {
    Add-Type -AssemblyName PresentationFramework
    [void][System.Windows.MessageBox]::Show("Não foi possível iniciar o instalador visível. $($_.Exception.Message)",'ez3D Setup','OK','Error')
    exit 1
  }
  exit 0
}
$installRoot = Join-Path $env:LOCALAPPDATA 'Programs\ez3D'
$runtime = Join-Path $installRoot 'Runtime'
$modelsRoot = Join-Path $runtime 'Models'
$kimodoAssets = Join-Path $runtime 'KimodoAssets'
$skinTokens = Join-Path $runtime 'SkinTokens'
$workRoot = Join-Path $env:LOCALAPPDATA 'ez3D\Work'
$logPath = Join-Path $env:LOCALAPPDATA 'ez3D\install.log'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null
Start-Transcript -Path $logPath -Append | Out-Null

function Stop-Install([string]$Message) {
  Write-Host "`nERRO: $Message" -ForegroundColor Red
  Write-Host "Log: $logPath"
  Read-Host 'Pressione Enter para fechar'
  exit 1
}

try {
  Write-Host 'Instalador do ez3D' -ForegroundColor Cyan
  Write-Host 'Este pacote baixa código e modelos oficiais. Nenhum token é enviado junto no instalador.'
  Write-Host @"

O que este instalador faz:
  1. Copia o ez3D para %LOCALAPPDATA%\Programs\ez3D e cria um atalho no menu Iniciar.
  2. Baixa o Python 3.11.9 oficial (nuget.org) para dentro dessa pasta. Não instala Python no Windows.
  3. Se faltar, instala o Ubuntu 24.04 no WSL 2 (recurso da Microsoft). Só aqui o Windows pede permissão de administrador.
  4. Dentro do Ubuntu, baixa programas e bibliotecas de IA, como administrador (root) apenas do Ubuntu.
  5. Pede seu token READ do Hugging Face e baixa os modelos de IA.
Ele não desativa o antivírus nem muda a política de segurança do Windows. Código-fonte: https://github.com/A-bps/ez3D
"@
  if ((Read-Host 'Continuar a instalação? (s/N)') -notmatch '^[sS]') { Write-Host 'Instalação cancelada. Nada foi instalado.'; exit 0 }
  $gpu = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
  if (!$gpu) { Stop-Install 'Driver NVIDIA não encontrado. Instale o driver NVIDIA atual e execute o instalador novamente.' }
  $vramText = & $gpu.Source --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1
  $vram = 0; [void][int]::TryParse(([string]$vramText).Trim(), [ref]$vram)
  # Pico medido numa RTX 3090 (TRELLIS.2 Máxima, modo staged): ~8,1 GB. Abaixo de 10 GB pode faltar memória.
  if ($vram -lt 10000) { Write-Host "Aviso: a GPU reporta $vram MiB. Recomendado 10 GB ou mais; a geração pode falhar por falta de memória." -ForegroundColor Yellow }
  $cap = [string](& $gpu.Source --query-gpu=compute_cap --format=csv,noheader 2>$null | Select-Object -First 1)
  if ([double]::TryParse($cap.Trim(), [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$null) -and [double]::Parse($cap.Trim(), [Globalization.CultureInfo]::InvariantCulture) -ge 10) { Write-Host 'Aviso: placas RTX 50 (Blackwell) ainda não são suportadas por esta versão (PyTorch 2.6 / CUDA 12.4).' -ForegroundColor Yellow }
  $pythonEnv = Join-Path $runtime 'PythonEnv'
  $freshInstall = !(Test-Path $pythonEnv)
  $freeGB = [math]::Floor((Get-PSDrive -Name ([IO.Path]::GetPathRoot($env:LOCALAPPDATA).Substring(0,1))).Free / 1GB)
  if ($freshInstall -and $freeGB -lt 80) { Stop-Install "Há $freeGB GB livres na unidade do Windows. Para uma instalação nova, reserve pelo menos 80 GB para modelos, runtimes e compilação." }

  New-Item -ItemType Directory -Force -Path $installRoot,$runtime,$modelsRoot,$kimodoAssets,$workRoot | Out-Null
  $archive = Join-Path $setupDir 'ez3DPayload.zip'
  if (!(Test-Path $archive)) { Stop-Install 'Pacote de arquivos do Studio ausente dentro do instalador.' }
  Write-Host 'Instalando os arquivos do Studio…'
  Expand-Archive -LiteralPath $archive -DestinationPath $installRoot -Force
  Copy-Item -LiteralPath (Join-Path $setupDir 'Start-ez3D.bat') -Destination $installRoot -Force

  $venvPython = Join-Path $runtime 'PythonEnv\Scripts\python.exe'
  $pythonExe = Join-Path $runtime 'Python\python.exe'
  if (!(Test-Path $venvPython) -and !(Test-Path $pythonExe)) {
    # Pacote oficial "python" (Python Software Foundation) no NuGet: só é descompactado, sem instalar nada no Windows.
    Write-Host 'Baixando o Python 3.11.9 para a pasta do ez3D (17 MB)…'
    $pyZip = Join-Path $runtime 'python-3.11.9.zip'
    $pyTemp = Join-Path $runtime 'PythonDownload'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri 'https://api.nuget.org/v3-flatcontainer/python/3.11.9/python.3.11.9.nupkg' -OutFile $pyZip
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $pyZip).Hash -ne '9283876D58C017E0E846F95B490DA3BCA0FC0A6EE1134B2870677CFB7EEC3C67') { Remove-Item -LiteralPath $pyZip -Force; Stop-Install 'O arquivo do Python não confere com o original. Rode o instalador novamente.' }
    Expand-Archive -LiteralPath $pyZip -DestinationPath $pyTemp -Force
    Move-Item -LiteralPath (Join-Path $pyTemp 'tools') -Destination (Join-Path $runtime 'Python')
    Remove-Item -LiteralPath $pyTemp,$pyZip -Recurse -Force
  }
  if (!(Test-Path $venvPython)) {
    & $pythonExe -m venv (Join-Path $runtime 'PythonEnv')
    if ($LASTEXITCODE -ne 0) { Stop-Install 'Não foi possível criar o ambiente Python do Studio.' }
  }
  Write-Host 'Instalando o aplicativo e as bibliotecas de preparação/exportação…'
  & $venvPython -m pip install --upgrade pip
  & $venvPython -m pip install -r (Join-Path $setupDir 'windows-requirements.txt')
  if ($LASTEXITCODE -ne 0) { Stop-Install 'Falha ao instalar as dependências Python do Studio.' }

  $configPath = Join-Path $installRoot 'Resources\Studio\studio_config.json'
  if (!(Test-Path $configPath)) { Stop-Install 'Arquivos do Studio não foram extraídos corretamente.' }
  $installDrive = [IO.Path]::GetPathRoot($installRoot).Substring(0,1).ToLowerInvariant()
  $relative = $installRoot.Substring(3).Replace('\','/')
  $wslInstall = "/mnt/$installDrive/$relative"
  $modelRelative = (Join-Path $modelsRoot 'TRELLIS.2').Substring(3).Replace('\','/')
  $wslTrellisModel = "/mnt/$installDrive/$modelRelative"
  $config = [ordered]@{
    work_root=$workRoot
    image_python=$venvPython
    mesh_python=$venvPython
    comfy_root=''
    comfy_url='http://127.0.0.1:8189'
    kimodo_root=$kimodoAssets
    kimodo_backend='wsl-vulkan'
    kimodo_wsl_binary='/opt/ez3d/Kimodo/build/release/kmd-generate'
    trellis_model=$wslTrellisModel
    tokenrig_root=$skinTokens
    engine=''
    project_roots=@()
    projects=@()
    port=8766
  }
  $config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding utf8

  $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
  if (!$wsl) { Stop-Install 'WSL não está disponível. Ative e instale o WSL 2, reinicie o Windows e rode este instalador novamente.' }
  $distros = ((& $wsl.Source --list --quiet 2>$null) -join "`n") -replace "`0",''
  if ($distros -notmatch '(?m)^Ubuntu-24\.04\s*$') {
    Write-Host 'Instalando Ubuntu 24.04 no WSL 2, sem pedir usuário nem senha. O Windows pode pedir permissão e reinicialização…' -ForegroundColor Yellow
    # --no-launch não abre o Ubuntu, então a tela de criar usuário não aparece. O ez3D usa o root do Ubuntu.
    Start-Process -FilePath $wsl.Source -Verb RunAs -ArgumentList @('--install','-d','Ubuntu-24.04','--no-launch') -Wait
    $distros = ((& $wsl.Source --list --quiet 2>$null) -join "`n") -replace "`0",''
    if ($distros -notmatch '(?m)^Ubuntu-24\.04\s*$') {
      # Versões do WSL em que o pacote é baixado, mas só é registrado pelo próprio lançador.
      $launcher = Get-Command ubuntu2404.exe -ErrorAction SilentlyContinue
      if ($launcher) { & $launcher.Source install --root }
    }
  }
  $ready = $false
  try { & $wsl.Source -d Ubuntu-24.04 -u root -- true 2>$null; $ready = ($LASTEXITCODE -eq 0) } catch {}
  if (!$ready) {
    Write-Host 'O Ubuntu ainda não está pronto. Reinicie o Windows e execute ez3D-Setup.exe novamente.' -ForegroundColor Yellow
    Read-Host 'Pressione Enter para fechar'
    exit 0
  }

  Write-Host 'Preparando CUDA, TRELLIS.2, TokenRig e Kimodo dentro do Ubuntu/WSL…' -ForegroundColor Cyan
  $wslScript = "$wslInstall/Resources/Studio/install_runtime_wsl.sh"
  $installRootFwd = $installRoot.Replace('\','/'); $modelsRootFwd = $modelsRoot.Replace('\','/')
  & $wsl.Source -d Ubuntu-24.04 -u root -- bash $wslScript $installRootFwd $modelsRootFwd
  if ($LASTEXITCODE -ne 0) { Stop-Install 'Falha na preparação do ambiente WSL/GPU. O log e a tela mostram a etapa que parou.' }

  Write-Host 'Modelos faciais têm termos e acesso individuais no Hugging Face.'
  Start-Process 'https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m'
  Start-Process 'https://huggingface.co/briaai/RMBG-2.0'
  Write-Host 'Aceite os termos desses dois modelos na conta da pessoa que usará o Studio. Depois cole no prompt o token READ dessa conta; ele fica apenas no perfil local.' -ForegroundColor Yellow
  Write-Host 'Ao colar o token, NADA aparece na tela: é normal, não travou. Cole com o botão direito do mouse e pressione Enter.' -ForegroundColor Yellow
  $hf = Join-Path $runtime 'PythonEnv\Scripts\hf.exe'
  & $hf auth login
  if ($LASTEXITCODE -ne 0) { Stop-Install 'Login Hugging Face não foi concluído.' }

  $wslModels = ((& $wsl.Source -d Ubuntu-24.04 -- wslpath -a ($modelsRoot.Replace('\','/'))) -join '').Trim()
  $prepare = Join-Path $installRoot 'Resources\Studio\prepare_trellis_models.py'
  Write-Host 'Baixando os pesos de geração e os modelos auxiliares de imagem. Esta etapa baixa vários gigabytes…' -ForegroundColor Cyan
  & $venvPython $prepare --models-root $modelsRoot --work-root $workRoot --wsl-models-root $wslModels
  if ($LASTEXITCODE -ne 0) { Stop-Install 'Download ou validação do TRELLIS.2 falhou. Confira o acesso Hugging Face e rode o instalador novamente.' }

  Write-Host "`nOpcional: gerador Hunyuan3D (várias fotos). Baixa vários GB a mais." -ForegroundColor Cyan
  Write-Host 'Licença da Tencent: NÃO vale na União Europeia, no Reino Unido nem na Coreia do Sul. Veja THIRD-PARTY.md.' -ForegroundColor Yellow
  if ((Read-Host 'Instalar o Hunyuan3D agora? (s/N)') -match '^[sS]') {
    & $wsl.Source -d Ubuntu-24.04 -u root -- bash "$wslInstall/Resources/Studio/install_hunyuan_mv.sh"
    if ($LASTEXITCODE -ne 0) { Write-Host 'O Hunyuan3D não foi instalado. O ez3D funciona normalmente com o TRELLIS.2.' -ForegroundColor Yellow }
  }
  $shortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'ez3D.lnk'
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($shortcutPath)
  $shortcut.TargetPath = Join-Path $installRoot 'Runtime\PythonEnv\Scripts\pythonw.exe'; $shortcut.Arguments = '"' + (Join-Path $installRoot 'Resources\Studio\desktop_app.py') + '"'
  $shortcut.WorkingDirectory = $installRoot
  $shortcut.IconLocation = Join-Path $installRoot 'Resources\Studio\ez3d.ico'
  $shortcut.Save()
  Write-Host "`nInstalação concluída. Abra o ez3D pelo atalho do menu Iniciar. Arquivos: $installRoot" -ForegroundColor Green
  Write-Host 'A primeira geração ainda carrega os pesos na GPU e pode demorar alguns minutos.'
  Read-Host 'Pressione Enter para fechar'
} catch {
  Write-Host "`nErro na instalação: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "Log: $logPath"
  Read-Host 'Pressione Enter para fechar'
  exit 1
} finally {
  try { Stop-Transcript | Out-Null } catch {}
}
