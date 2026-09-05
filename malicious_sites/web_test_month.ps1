# web_test_month.ps1
# Usage: powershell -File .\web_test_month.ps1
#    or: pwsh -File .\web_test_month.ps1

$Csv = "month.csv"

$phraseMap = @{
    '200' = 'OK'
    '201' = 'Created'
    '204' = 'No Content'
    '301' = 'Redirect'
    '302' = 'Redirect'
    '303' = 'Redirect'
    '307' = 'Redirect'
    '308' = 'Redirect'
    '400' = 'Bad Request'
    '401' = 'Unauthorized'
    '403' = 'Forbidden'
    '404' = 'Not Found'
    '500' = 'Internal Server Error'
    '502' = 'Bad Gateway'
    '503' = 'Service Unavailable'
    '000' = 'Connection Failed'
}

# Ctrl+C 시 bash trap과 같이 종료 메시지 출력
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
    Write-Host "Stopping..."
}

$urls = New-Object System.Collections.Generic.List[string]
Get-Content -LiteralPath $Csv | ForEach-Object {
    $line = $_.Trim()
    if (-not $line) { return }
    $cols = $line -split ',', 5
    if ($cols.Count -ge 4 -and $cols[2] -eq 'url') {
        $urls.Add($cols[3])
    }
}

try {
    while ($true) {
        foreach ($url in $urls) {
            # Windows에 기본 내장된 curl.exe 사용
            # (PowerShell의 curl 별칭 Invoke-WebRequest와 혼동하지 않도록 .exe 명시)
            $output = & curl.exe --connect-timeout 5 -m 10 -sk -o NUL -w '%{http_code} %{size_download}' $url 2>$null
            $parts = -split $output.Trim()
            $httpCode = $parts[0]
            $bytes    = $parts[1]

            $phrase = $phraseMap[$httpCode]
            if ($phrase) {
                Write-Host "[ACCESS] $url   --- $httpCode $phrase"
            } else {
                Write-Host "[ACCESS] $url   --- $httpCode"
            }
            Write-Host "[TRAFFIC] $bytes"
            Start-Sleep -Seconds 1
        }
    }
} finally {
    Write-Host "Stopping..."
}
