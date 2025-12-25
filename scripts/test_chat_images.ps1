param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [int]$TopK = 8,
  [string]$Message = "请给我返回医院建筑设计相关的图纸/平面图（能带图更好），优先找《建筑设计资料集 第6册 医疗》里的示例。"
)

$uri = "$BaseUrl/api/v1/chat"

$body = @{
  message            = $Message
  top_k              = $TopK
  include_citations  = $true
  include_diagnostics= $true
} | ConvertTo-Json -Depth 10

Write-Host "POST $uri" -ForegroundColor Cyan
Write-Host "message: $Message" -ForegroundColor Cyan

try {
  $resp = Invoke-RestMethod -Uri $uri -Method Post -ContentType "application/json" -Body $body
} catch {
  Write-Host "Request failed: $($_.Exception.Message)" -ForegroundColor Red
  throw
}

Write-Host "`n=== images[] ===" -ForegroundColor Yellow
$resp.images

Write-Host "`n=== message (tail) ===" -ForegroundColor Yellow
$msg = [string]$resp.message
if ($msg.Length -gt 500) {
  $msg.Substring($msg.Length - 500)
} else {
  $msg
}

Write-Host "`n=== citations (first 5) ===" -ForegroundColor Yellow
$resp.citations | Select-Object -First 5 | Format-List

