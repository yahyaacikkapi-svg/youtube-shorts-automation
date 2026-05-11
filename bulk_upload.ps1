# Bulk upload: 28 video, 14 gun, gunluk 2 slot (TR 19:00 + 01:00)
# Calistirma: .\bulk_upload.ps1 -From 0 -Count 6
# Gunluk kota: 6 video max (YouTube API 10k unit/gun, upload 1600 unit)
#
# Gun 1 (bugun):   -From 0  -Count 6   -> slot 1-6
# Gun 2 (yarin):   -From 6  -Count 6   -> slot 7-12
# Gun 3:           -From 12 -Count 6   -> slot 13-18
# Gun 4:           -From 18 -Count 6   -> slot 19-24
# Gun 5:           -From 24 -Count 4   -> slot 25-28

param(
    [int]$From = 0,
    [int]$Count = 6
)

# TR=UTC+3. TR 19:00 = UTC 16:00. TR 01:00 = UTC 22:00 (onceki UTC gun)
# 28 slot: 12 Mayis TR - 25 Mayis TR (19:00 ve 01:00)
$slots = @(
    "2026-05-12T16:00:00.000Z",  # May 12 19:00 TR
    "2026-05-12T22:00:00.000Z",  # May 13 01:00 TR
    "2026-05-13T16:00:00.000Z",  # May 13 19:00 TR
    "2026-05-13T22:00:00.000Z",  # May 14 01:00 TR
    "2026-05-14T16:00:00.000Z",  # May 14 19:00 TR
    "2026-05-14T22:00:00.000Z",  # May 15 01:00 TR
    "2026-05-15T16:00:00.000Z",  # May 15 19:00 TR
    "2026-05-15T22:00:00.000Z",  # May 16 01:00 TR
    "2026-05-16T16:00:00.000Z",  # May 16 19:00 TR
    "2026-05-16T22:00:00.000Z",  # May 17 01:00 TR
    "2026-05-17T16:00:00.000Z",  # May 17 19:00 TR
    "2026-05-17T22:00:00.000Z",  # May 18 01:00 TR
    "2026-05-18T16:00:00.000Z",  # May 18 19:00 TR
    "2026-05-18T22:00:00.000Z",  # May 19 01:00 TR
    "2026-05-19T16:00:00.000Z",  # May 19 19:00 TR
    "2026-05-19T22:00:00.000Z",  # May 20 01:00 TR
    "2026-05-20T16:00:00.000Z",  # May 20 19:00 TR
    "2026-05-20T22:00:00.000Z",  # May 21 01:00 TR
    "2026-05-21T16:00:00.000Z",  # May 21 19:00 TR
    "2026-05-21T22:00:00.000Z",  # May 22 01:00 TR
    "2026-05-22T16:00:00.000Z",  # May 22 19:00 TR
    "2026-05-22T22:00:00.000Z",  # May 23 01:00 TR
    "2026-05-23T16:00:00.000Z",  # May 23 19:00 TR
    "2026-05-23T22:00:00.000Z",  # May 24 01:00 TR
    "2026-05-24T16:00:00.000Z",  # May 24 19:00 TR
    "2026-05-24T22:00:00.000Z",  # May 25 01:00 TR
    "2026-05-25T16:00:00.000Z",  # May 25 19:00 TR
    "2026-05-25T22:00:00.000Z"   # May 26 01:00 TR
)

$end = [Math]::Min($From + $Count, $slots.Length)
$batch = $slots[$From..($end - 1)]

Write-Host "=== Bulk Upload: slot $($From+1)-$end / $($slots.Length) ==="
Write-Host ""

$i = $From
foreach ($slot in $batch) {
    $i++
    Write-Host "--- Video $i / $($slots.Length) -> $slot ---"
    python shorts_automation.py --privacy private --publish-at-utc $slot
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[HATA] Video $i basarisiz oldu. Devam ediliyor..."
    }
    Write-Host ""
}

Write-Host "=== Bitti. $($batch.Length) video yuklendi. ==="
if ($end -lt $slots.Length) {
    Write-Host "Yarin calistir: .\bulk_upload.ps1 -From $end -Count 6"
}
