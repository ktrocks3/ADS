param(
    [int]$num_clients = 10,
    [int]$delay = 30
)

$algo = "rr"
$composeFile = "docker-compose.yml"

Write-Host ">>> Starting load balancer test with $num_clients clients (delay ${delay}s, algo $algo)"

# Start up
docker compose -f $composeFile up --build -d --scale client=$num_clients

Write-Host "Waiting ${delay}s for warm-up..."
Start-Sleep -Seconds $delay

# Phase B: stop server1
Write-Host ">>> Stopping server1"
docker compose stop server1
Start-Sleep -Seconds $delay

# Phase C: stop server2
Write-Host ">>> Stopping server2"
docker compose stop server2
Start-Sleep -Seconds $delay

# Phase D: restart both
Write-Host ">>> Starting server1 and server2 back up"
docker compose start server1, server2
Start-Sleep -Seconds $delay

# Show results
Write-Host ">>> Test complete. Container status:"
docker compose ps

Write-Host ">>> Done. Check your client CSVs for results."
