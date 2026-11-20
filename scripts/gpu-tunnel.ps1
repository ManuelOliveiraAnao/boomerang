# Opens the SSH tunnel to the Leaseweb server (docs/17-leaseweb-gpu.md).
# Laptop 127.0.0.1:11435  ->  server 127.0.0.1:11434 (Ollama, never exposed to the internet).
# Needs a "boomerang-gpu" entry in ~/.ssh/config (docs/17 step 3). Leave this window open; Ctrl+C closes it.
# Reconnects by itself if the network drops.
param([string]$HostAlias = "boomerang-gpu", [int]$LocalPort = 11435)

while ($true) {
    Write-Host "Tunnel: 127.0.0.1:$LocalPort -> ${HostAlias}:11434 (Ctrl+C to stop)"
    ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 `
        -L "${LocalPort}:127.0.0.1:11434" $HostAlias
    Write-Host "Tunnel closed (exit $LASTEXITCODE). Reconnecting in 3 s..."
    Start-Sleep -Seconds 3
}
