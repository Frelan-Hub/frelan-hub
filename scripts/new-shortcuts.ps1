<#
.SYNOPSIS
    Create the Desktop and Start Menu shortcuts for the dashboard.

.DESCRIPTION
    Called by install.bat. Kept as a PowerShell file rather than an inline
    -Command string because shortcut creation needs COM and quoting that
    batch escaping mangles.

    Both shortcuts point at run_ui.bat and set WorkingDirectory to the
    repository root: the runtime resolves main.py, missions\, inputs\ and
    outputs\ relative to the working directory, so a shortcut without it
    would start the app against the wrong folder.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

$ErrorActionPreference = 'Stop'

$target = Join-Path $Root 'run_ui.bat'
if (-not (Test-Path $target)) {
    Write-Host "      run_ui.bat not found at $target - skipping shortcuts."
    exit 0
}

# A .lnk can only take an .ico, never a .png, so the brand icon is applied
# only when a converted one is actually present.
$icon = Join-Path $Root 'assets\frelan-logo.ico'

$destinations = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop'))  'AI-Conductor B.lnk')
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'AI-Conductor B.lnk')
)

$shell = New-Object -ComObject WScript.Shell
foreach ($destination in $destinations) {
    try {
        $link = $shell.CreateShortcut($destination)
        $link.TargetPath       = $target
        $link.WorkingDirectory = $Root
        $link.Description      = 'AI-Conductor B Runtime - control plane dashboard'
        if (Test-Path $icon) { $link.IconLocation = $icon }
        $link.Save()
        Write-Host "      created $destination"
    }
    catch {
        # A locked or redirected folder is a nuisance, not a failed install.
        Write-Host "      could not create $destination - $($_.Exception.Message)"
    }
}
