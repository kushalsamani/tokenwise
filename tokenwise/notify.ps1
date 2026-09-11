# tokenwise desktop notification, Windows.
#
# The macOS build shells out to osascript for this. Windows has no equivalent one-liner, so the toast lives
# in its own file: _common.notify() launches it with a literal argument list, and MANIFEST.sha256 hashes it,
# so the script that runs is the script that was reviewed. It shows a notification and exits. It reads
# nothing, writes nothing, and makes no network call.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File notify.ps1 "<title>" "<message>"

param(
  [string]$Title = 'tokenwise',
  [string]$Message = ''
)

$ErrorActionPreference = 'Stop'

function Show-Toast {
  param([string]$Title, [string]$Message)
  # Windows 10/11 native toast. No module to install; the shell's own AppID is borrowed so the notification
  # is attributable and lands in Action Center.
  [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
  [void][Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]
  [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]

  $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
  $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
           [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $nodes = $xml.GetElementsByTagName('text')
  [void]$nodes.Item(0).AppendChild($xml.CreateTextNode($Title))
  [void]$nodes.Item(1).AppendChild($xml.CreateTextNode($Message))
  $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
}

function Show-Balloon {
  param([string]$Title, [string]$Message)
  # Fallback for machines where the toast API is unavailable (older builds, some Server SKUs). A tray balloon
  # needs its owner process alive while it shows, hence the short sleep; the caller allows for it.
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $icon = New-Object System.Windows.Forms.NotifyIcon
  try {
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.Visible = $true
    $icon.ShowBalloonTip(8000, $Title, $Message, [System.Windows.Forms.ToolTipIcon]::Info)
    Start-Sleep -Milliseconds 2500
  } finally {
    $icon.Dispose()
  }
}

try {
  Show-Toast -Title $Title -Message $Message
} catch {
  try {
    Show-Balloon -Title $Title -Message $Message
  } catch {
    # A notification is a courtesy. The hook has already told Claude to say the same thing on screen, so
    # failing here must never look like an error to the user.
    exit 0
  }
}
exit 0
