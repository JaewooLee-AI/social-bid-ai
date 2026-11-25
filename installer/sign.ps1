# Authenticode 코드 서명 (CI 선택 단계). 환경 변수:
#   WINDOWS_CERT_PFX_BASE64  PFX 인증서(base64)
#   WINDOWS_CERT_PASSWORD    PFX 암호
param([Parameter(Mandatory = $true)][string[]]$Files)
$ErrorActionPreference = "Stop"

if (-not $env:WINDOWS_CERT_PFX_BASE64) { throw "WINDOWS_CERT_PFX_BASE64 is not set" }
$pfx = Join-Path $env:RUNNER_TEMP "codesign.pfx"
[IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_CERT_PFX_BASE64))
try {
  $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
  if (-not $signtool) { throw "signtool.exe not found" }
  foreach ($f in $Files) {
    & $signtool sign /f $pfx /p $env:WINDOWS_CERT_PASSWORD /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $f
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $f" }
    $sig = Get-AuthenticodeSignature -FilePath $f
    if ($sig.Status -ne "Valid") { throw "Signature not valid after signing: $f ($($sig.Status))" }
  }
} finally {
  Remove-Item $pfx -Force -ErrorAction SilentlyContinue
}
