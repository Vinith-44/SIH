# Downloads CAVIAR shopping-centre clips (public, CC BY-SA, ground truth included) into videos\entrance\caviar
# Run in PowerShell:   cd C:\SIH ;  powershell -ExecutionPolicy Bypass -File .\download_caviar.ps1
# Credit in PPT: "EC Funded CAVIAR project/IST 2001 37540"

$ProgressPreference = 'SilentlyContinue'   # makes Invoke-WebRequest much faster
$base = "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA2"
$out  = Join-Path $PSScriptRoot "videos\entrance\caviar"
New-Item -ItemType Directory -Force -Path $out | Out-Null

# scenario, corridor-XML, front-XML
$clips = @(
  @("OneStopEnter1",           "cose1gt.xml",  "fose1gt.xml"),
  @("OneStopEnter2",           "cose2gt.xml",  "fose2gt.xml"),
  @("EnterExitCrossingPaths1", "ceecp1gt.xml", "feecp1gt.xml"),
  @("EnterExitCrossingPaths2", "ceecp2gt.xml", "feecp2gt.xml"),
  @("OneShopOneWait1",         "cosow1gt.xml", "fosow1gt.xml"),
  @("OneShopOneWait2",         "cosow2gt.xml", "fosow2gt.xml"),
  @("ThreePastShop1",          "c3ps1gt.xml",  "f3ps1gt.xml"),
  @("WalkByShop1",             "cwbs1gt.xml",  "fwbs1gt.xml")
)

$ok = 0; $fail = @()
foreach ($c in $clips) {
  $name = $c[0]
  foreach ($view in @(@("cor", $c[1]), @("front", $c[2]))) {
    $dir = "$name$($view[0])"
    foreach ($file in @("$dir.mpg", $view[1])) {
      $url = "$base/$dir/$file"
      $dst = Join-Path $out $file
      if (Test-Path $dst) { Write-Host "skip (exists) $file"; $ok++; continue }
      try {
        Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing -TimeoutSec 300
        Write-Host "OK   $file"; $ok++
      } catch {
        Write-Host "FAIL $url" -ForegroundColor Red; $fail += $url
        if (Test-Path $dst) { Remove-Item $dst }
      }
    }
  }
}

"CAVIAR shopping-centre clips - EC Funded CAVIAR project/IST 2001 37540 - CC BY-SA`nSource: https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/`nGround truth: https://homepages.inf.ed.ac.uk/rbf/CAVIAR/gt.htm" |
  Out-File -Encoding utf8 (Join-Path $out "SOURCE.txt")

Write-Host "`nDone: $ok files OK, $($fail.Count) failed."
if ($fail.Count -gt 0) {
  Write-Host "Failed URLs (open https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/ and download these by hand):"
  $fail | ForEach-Object { Write-Host "  $_" }
}
