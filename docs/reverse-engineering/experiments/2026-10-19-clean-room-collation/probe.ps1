# Black-box probe: send DAX queries to the workspace engine of the Power BI
# Desktop running on this machine and print the answers. Nothing else is read.
# Usage: powershell -File probe.ps1 <queries.txt> <answers.tsv>
# queries.txt: one "id<TAB>DAX expression" per line; each is sent as
# EVALUATE ROW("v", <expression>) and the single value is recorded.
param([string]$In, [string]$Out)
$bin = 'F:\PowerBI\Microsoft Power BI Desktop\bin'
[void][System.Reflection.Assembly]::LoadFrom((Join-Path $bin 'Microsoft.PowerBI.AdomdClient.dll'))
$ws = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces\*\Data\msmdsrv.port.txt" |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
$port = (Get-Content $ws.FullName -Encoding Unicode).Trim()
$cn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port")
$cn.Open()
$ver = $cn.ServerVersion
$lines = [System.IO.File]::ReadAllLines($In, [System.Text.Encoding]::UTF8)
$res = New-Object System.Collections.Generic.List[string]
$res.Add("# engine $ver port $port")
foreach ($ln in $lines) {
    if (-not $ln.Trim() -or $ln.StartsWith('#')) { continue }
    $id, $expr = $ln.Split("`t", 2)
    $cmd = $cn.CreateCommand()
    $cmd.CommandText = "EVALUATE ROW(""v"", $expr)"
    try {
        $r = $cmd.ExecuteReader()
        $v = if ($r.Read()) { $r.GetValue(0) } else { '' }
        $r.Close()
    } catch { $v = 'ERR: ' + $_.Exception.Message.Split("`n")[0] }
    $res.Add("$id`t$expr`t$v")
}
$cn.Close()
[System.IO.File]::WriteAllLines($Out, $res, (New-Object System.Text.UTF8Encoding($false)))
