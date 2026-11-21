# Minimal local + public webhook smoke for refactor regression.
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File deploy\smoke.ps1
$ErrorActionPreference = 'Stop'

function Invoke-Webhook($base) {
    $url = "$base/webhook"
    $response = Invoke-WebRequest -Uri $url -Method Post -ContentType 'application/json' `
        -Body '{"action":"get_venues"}' -UseBasicParsing -TimeoutSec 20
    if ($response.StatusCode -ne 200) {
        throw "Webhook HTTP $($response.StatusCode) at $url"
    }
    $payload = $response.Content | ConvertFrom-Json
    if (-not $payload.venues -or $payload.venues.Count -lt 1) {
        throw "get_venues returned empty venues at $url"
    }
    foreach ($venue in $payload.venues) {
        if ($venue.id -in 4, 5, 6 -and $null -ne $venue.on_duty_count) {
            $codes = @()
            if ($venue.technician_photos) {
                $codes = @($venue.technician_photos.PSObject.Properties.Name)
            }
            if ($venue.on_duty_count -gt 0 -and $codes.Count -eq 0) {
                Write-Warning "Venue $($venue.id) on_duty_count=$($venue.on_duty_count) but no technician_photos keys."
            }
        }
    }
    Write-Host "OK get_venues ($($payload.venues.Count) venues): $url"
}

function Invoke-Hotels($base) {
    $url = "$base/webhook"
    $response = Invoke-WebRequest -Uri $url -Method Post -ContentType 'application/json' `
        -Body '{"action":"get_hotels","page":1}' -UseBasicParsing -TimeoutSec 20
    if ($response.StatusCode -ne 200) {
        throw "get_hotels HTTP $($response.StatusCode)"
    }
    Write-Host "OK get_hotels: $url"
}

Write-Host 'Smoke: local API'
Invoke-Webhook 'http://127.0.0.1:8000'
try { Invoke-Hotels 'http://127.0.0.1:8000' } catch {
    Write-Warning "hotels_list skipped: $($_.Exception.Message)"
}

Write-Host 'Smoke: public tunnel'
Invoke-Webhook 'https://app.haotianzhushou.com/api'
Write-Host 'Smoke complete.'
