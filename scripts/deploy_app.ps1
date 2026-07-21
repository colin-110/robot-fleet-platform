# Refresh PATH to load OpenSSH tools
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

param(
    [Parameter(Mandatory=$true)]
    [string]$IP,

    [string]$KeyFile = "fleet-key.pem",

    [string]$User = "ubuntu"
)

Write-Host "=== Robot Fleet Platform: Cloud Deployer ===" -ForegroundColor Cyan
Write-Host "Target: ${User}@${IP}" -ForegroundColor Yellow

# 1. Compress workspace folders
Write-Host "Compressing local project files..."
if (Test-Path "fleet-platform.tar.gz") {
    Remove-Item "fleet-platform.tar.gz"
}

# Run tar (tar is built into Windows 10/11 PowerShell)
tar --exclude='node_modules' --exclude='.git' --exclude='.gemini' --exclude='venv' --exclude='.venv' --exclude='dist' --exclude='__pycache__' -czf fleet-platform.tar.gz backend frontend simulator docker-compose.yml
if (-not (Test-Path "fleet-platform.tar.gz")) {
    Write-Error "Failed to compress archive."
    exit 1
}
Write-Host "Archive created: fleet-platform.tar.gz" -ForegroundColor Green

# 2. Set key permissions (standard OpenSSH check workaround for Windows)
icacls $KeyFile /inheritance:r /grant:r "${env:USERNAME}:R" > $null

# 3. Transfer archive to EC2
Write-Host "Uploading project archive to EC2 ($IP)..."
$scp_cmd = "scp -o StrictHostKeyChecking=no -i $KeyFile fleet-platform.tar.gz ${User}@${IP}:/home/${User}/"
Invoke-Expression $scp_cmd
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to transfer project archive. Make sure instance is reachable."
    exit 1
}
Write-Host "Upload complete." -ForegroundColor Green

# 4. SSH and execute setup & build
Write-Host "Connecting to EC2 to install Docker and build containers..."
$ssh_setup = "sudo apt-get update -y && sudo apt-get install -y docker.io docker-compose-v2 && sudo systemctl start docker && sudo systemctl enable docker && sudo usermod -aG docker ${User}"
$ssh_extract = "tar -xzf fleet-platform.tar.gz"
$ssh_build = "sudo CORS_ORIGINS=http://localhost:5173,http://localhost:3000,http://${IP} docker compose up -d --build"

# Run setup commands
$ssh_cmd = "ssh -o StrictHostKeyChecking=no -i $KeyFile ${User}@${IP} `"${ssh_setup} && ${ssh_extract} && ${ssh_build}`""
Write-Host "Executing remote build sequence (this may take a few minutes)..."
Invoke-Expression $ssh_cmd

if ($LASTEXITCODE -eq 0) {
    Write-Host "=========================================" -ForegroundColor Green
    Write-Host "DEPLOYMENT COMPLETE!" -ForegroundColor Green
    Write-Host "Web Dashboard is now live at:"
    Write-Host "http://${IP}" -ForegroundColor Yellow
    Write-Host "=========================================" -ForegroundColor Green
} else {
    Write-Error "Deployment failed during remote build."
}
