param(
  [int]$SessionId = -1,
  [string]$TitleB64,
  [string]$TextB64
)

function DecodeB64($b64) {
  if (-not $b64) { return "" }
  $bytes = [System.Convert]::FromBase64String($b64)
  return [System.Text.Encoding]::Unicode.GetString($bytes)
}

# ---------------------------------------------------------------------------
# Mostra a janela WPF na SESSION ATUAL (deve rodar na sessao do usuario)
# ---------------------------------------------------------------------------
function Show-WpfPopup([string]$title, [string]$text) {
  Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

  $win = New-Object System.Windows.Window
  $win.Title = $title
  $win.Width = 480
  $win.Height = 390
  $win.WindowStartupLocation = 'CenterScreen'
  $win.WindowStyle = 'None'
  $win.ResizeMode = 'NoResize'
  $win.Topmost = $true
  $win.Background = 'White'
  $win.AllowsTransparency = $true

  # Sombra leve
  $win.Effect = New-Object System.Windows.Media.Effects.DropShadowEffect
  $win.Effect.BlurRadius = 18
  $win.Effect.ShadowDepth = 3
  $win.Effect.Opacity = 0.45

  $grid = New-Object System.Windows.Controls.Grid
  $grid.Background = 'White'
  $win.Content = $grid

  # Borda fina azul no topo (accent bar discreto, 6px, largura total da janela)
  $accent = New-Object System.Windows.Controls.Border
  $accent.Height = 6
  $accent.VerticalAlignment = 'Top'
  $accent.HorizontalAlignment = 'Stretch'
  $accent.Background = [System.Windows.Media.Brushes]::DodgerBlue
  $grid.Children.Add($accent)

  $contentStack = New-Object System.Windows.Controls.StackPanel
  $contentStack.Margin = '36,40,36,28'
  $contentStack.HorizontalAlignment = 'Center'
  $contentStack.VerticalAlignment = 'Center'
  $grid.Children.Add($contentStack)

  # --- LOGO (real) ---
  $logoPath = "C:\Windows\Temp\popup-logo.png"
  $logo = New-Object System.Windows.Controls.Image
  if (Test-Path $logoPath) {
    $bi = New-Object System.Windows.Media.Imaging.BitmapImage
    $bi.BeginInit()
    $bi.UriSource = ([uri]$logoPath)
    $bi.CacheOption = 'OnLoad'
    $bi.EndInit()
    $logo.Source = $bi
    $logo.Height = 110
    $logo.Stretch = 'Uniform'
    $logo.Margin = '0,0,0,8'
    $logo.HorizontalAlignment = 'Center'
  } else {
    $logo.Height = 42
    $logo.Width = 42
    $logo.HorizontalAlignment = 'Center'
    $fill = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.Color]::FromRgb(30,140,255))
    $logo.Source = $null
    $fallback = New-Object System.Windows.Controls.Border
    $fallback.Width = 42; $fallback.Height = 42
    $fallback.Background = $fill
    $fallback.CornerRadius = 21
    $fallback.Margin = '0,0,0,14'
    $fallback.HorizontalAlignment = 'Center'
    $fallbackText = New-Object System.Windows.Controls.TextBlock
    $fallbackText.Text = "TI"
    $fallbackText.Foreground = [System.Windows.Media.Brushes]::White
    $fallbackText.FontWeight = 'Bold'
    $fallbackText.FontSize = 14
    $fallbackText.HorizontalAlignment = 'Center'
    $fallbackText.VerticalAlignment = 'Center'
    $fallback.Child = $fallbackText
    $contentStack.Children.Add($fallback)
  }
  $contentStack.Children.Add($logo)

  # --- TITULO ---
  $tituloBlock = New-Object System.Windows.Controls.TextBlock
  $tituloBlock.Text = $title
  $tituloBlock.FontSize = 19
  $tituloBlock.FontWeight = 'Bold'
  $tituloBlock.FontFamily = 'Segoe UI'
  $tituloBlock.Foreground = [System.Windows.Media.Brushes]::DodgerBlue
  $tituloBlock.TextAlignment = 'Center'
  $tituloBlock.HorizontalAlignment = 'Center'
  $tituloBlock.Margin = '0,0,0,6'
  $contentStack.Children.Add($tituloBlock)

  # --- SUBTITULO ---
  $subBlock = New-Object System.Windows.Controls.TextBlock
  $subBlock.Text = "Mensagem do suporte de TI"
  $subBlock.FontSize = 11
  $subBlock.FontFamily = 'Segoe UI'
  $subBlock.Foreground = [System.Windows.Media.Brushes]::Gray
  $subBlock.TextAlignment = 'Center'
  $subBlock.HorizontalAlignment = 'Center'
  $subBlock.Margin = '0,0,0,20'
  $contentStack.Children.Add($subBlock)

  # --- MENSAGEM ---
  $msgBlock = New-Object System.Windows.Controls.TextBlock
  $msgBlock.Text = $text
  $msgBlock.FontSize = 14
  $msgBlock.FontFamily = 'Segoe UI'
  $msgBlock.Foreground = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.Color]::FromRgb(60,60,60))
  $msgBlock.TextWrapping = 'Wrap'
  $msgBlock.TextAlignment = 'Center'
  $msgBlock.HorizontalAlignment = 'Stretch'
  $msgBlock.Margin = '0,0,0,24'
  $contentStack.Children.Add($msgBlock)

  # --- BOTAO OK simples e funcional ---
  $btnOk = New-Object System.Windows.Controls.Button
  $btnOk.Content = "OK"
  $btnOk.Width = 130
  $btnOk.Height = 36
  $btnOk.HorizontalAlignment = 'Center'
  $btnOk.Background = [System.Windows.Media.Brushes]::DodgerBlue
  $btnOk.Foreground = [System.Windows.Media.Brushes]::White
  $btnOk.FontWeight = 'Bold'
  $btnOk.FontSize = 14
  $btnOk.FontFamily = 'Segoe UI'
  $btnOk.Cursor = 'Hand'

  $contentStack.Children.Add($btnOk)

  $script:okClicked = $false
  $btnOk.Add_Click({ $script:okClicked = $true; $win.Close() })

  $win.ShowDialog() | Out-Null
  if ($script:okClicked) { Write-Output "OK_CLICKED" } else { Write-Output "FECHADO" }
}

# ---------------------------------------------------------------------------
# Sobe o WPF na SESSION alvo usando CreateProcessAsUser
# ---------------------------------------------------------------------------
function Invoke-AsUserSession([int]$targetSession, [string]$titleB64, [string]$textB64) {
  Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WTS {
  [DllImport("wtsapi32.dll")] public static extern int WTSQueryUserToken(int sessionId, out IntPtr phToken);
  [DllImport("userenv.dll", SetLastError=true)] public static extern bool CreateEnvironmentBlock(out IntPtr env, IntPtr token, bool inherit);
  [DllImport("userenv.dll", SetLastError=true)] public static extern bool DestroyEnvironmentBlock(IntPtr env);
  [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern bool CreateProcessAsUser(IntPtr token, string app, string cmd, IntPtr pa, IntPtr pt, bool inherit, uint flags, IntPtr env, string cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
  [DllImport("kernel32.dll")] public static extern uint WaitForSingleObject(IntPtr h, uint ms);
  [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll")] public static extern bool GetExitCodeProcess(IntPtr h, out uint code);
}
public struct STARTUPINFO { public int cb; public string lpReserved; public string lpDesktop; public string lpTitle; public int dwX; public int dwY; public int dwXSize; public int dwYSize; public int dwXCountChars; public int dwYCountChars; public int dwFillAttribute; public int dwFlags; public short wShowWindow; public short cbReserved2; public IntPtr lpReserved2; public IntPtr hStdInput; public IntPtr hStdOutput; public IntPtr hStdErr; }
public struct PROCESS_INFORMATION { public IntPtr hProcess; public IntPtr hThread; public int dwProcessId; public int dwThreadId; }
"@

  $token = [IntPtr]::Zero
  if (-not [WTS]::WTSQueryUserToken($targetSession, [ref]$token) -or $token -eq [IntPtr]::Zero) {
    return $false
  }

  $env = [IntPtr]::Zero
  [WTS]::CreateEnvironmentBlock([ref]$env, $token, $false) | Out-Null

  $si = New-Object STARTUPINFO
  $si.cb = [System.Runtime.InteropServices.Marshal]::SizeOf([type]([STARTUPINFO]))
  $si.lpDesktop = 'winsta0\default'
  $pi = New-Object PROCESS_INFORMATION

  $cmdLine = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$($MyInvocation.MyCommand.Definition)`" -SessionId -1 -TitleB64 '$titleB64' -TextB64 '$textB64'"

  # 0x400 = CREATE_UNICODE_ENVIRONMENT (obrigatório ao passar o bloco de ambiente criado acima)
  $ok = [WTS]::CreateProcessAsUser($token, $null, $cmdLine, [IntPtr]::Zero, [IntPtr]::Zero, $false, 0x00000400, $env, $null, [ref]$si, [ref]$pi)
  if ($ok) {
    [WTS]::WaitForSingleObject($pi.hProcess, 120000) | Out-Null
    [WTS]::CloseHandle($pi.hProcess) | Out-Null
    [WTS]::CloseHandle($pi.hThread) | Out-Null
  }
  if ($env -ne [IntPtr]::Zero) { [WTS]::DestroyEnvironmentBlock($env) | Out-Null }
  [WTS]::CloseHandle($token) | Out-Null
  return $ok
}

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
$title = DecodeB64 $TitleB64
$text  = DecodeB64 $TextB64

if ($SessionId -eq -1 -or $SessionId -eq (Get-Process -Id $PID).SessionId) {
  Show-WpfPopup $title $text
  exit
}

$ok = Invoke-AsUserSession $SessionId $TitleB64 $TextB64
if ($ok) { Write-Output "ENVIADO sessao $SessionId" } else { Write-Output "FALHA sessao $SessionId" }
