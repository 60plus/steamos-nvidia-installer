# Run the existing Linux builder in an already configured build VM.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Guest,
    [Parameter(Mandatory)][string]$Repository,
    [Parameter(Mandatory)][string]$Image,
    [Parameter(Mandatory)][string]$WorkDirectory,
    [ValidateRange(1,65535)][int]$Port = 2222,
    [string[]]$BuilderArguments = @(),
    [switch]$BaseOnly
)
$ErrorActionPreference = 'Stop'
if ($Guest -notmatch '^[a-zA-Z0-9_][a-zA-Z0-9_.-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*$') {
    throw 'Guest must be user@hostname or user@IPv4-address.'
}
foreach ($path in @($Repository, $Image, $WorkDirectory)) {
    if (-not $path.StartsWith('/') -or $path.Contains("`n") -or $path.Contains("`r")) {
        throw 'Use absolute Linux paths without line breaks.'
    }
}
Get-Command ssh -ErrorAction Stop | Out-Null
function ConvertTo-BashArgument([string]$Value) {
    if ($Value.Contains([char]0)) { throw 'NUL is not allowed in arguments.' }
    return "'" + $Value.Replace("'", "'\''") + "'"
}
$repoArg = ConvertTo-BashArgument $Repository
$imageArg = ConvertTo-BashArgument $Image
$workArg = ConvertTo-BashArgument $WorkDirectory
$extra = ($BuilderArguments | ForEach-Object { ConvertTo-BashArgument $_ }) -join ' '
# A terminal lets sudo request the guest password normally. It is never stored.
if ($BaseOnly) {
    $command = "cd $repoArg && sudo bash tools/check-build-host.sh $imageArg $workArg && sudo bash ./steamos-nvidia-installer.sh --workdir $workArg $extra $imageArg"
} else {
    $command = "cd $repoArg && sudo bash tools/build-complete.sh --workdir $workArg $extra $imageArg"
}
Write-Host 'Building in the Linux VM. Compilation can be quiet for several minutes.'
Write-Host 'Leave this terminal and the VM running until the builder finishes.'
& ssh -t -p $Port $Guest $command
if ($LASTEXITCODE -ne 0) {
    throw "The VM build failed (exit $LASTEXITCODE). Keep its log/cache; do not flash the output."
}
Write-Host 'Build completed. Retrieve the output with scp and compare SHA256 hashes as described in the Windows guide.'
