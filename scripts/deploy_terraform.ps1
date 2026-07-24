# Refresh PATH
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

Write-Host "=== Robot Fleet Platform: Terraform Infrastructure Provisioning ===" -ForegroundColor Cyan

# 1. Check for Terraform
if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
    Write-Error "Terraform is not installed or not in PATH. Please install Terraform to continue."
    exit 1
}

$terraform_dir = Join-Path $PSScriptRoot "..\terraform"
Set-Location $terraform_dir

# 2. Generate or Ask for DB Password
$db_password = Read-Host -AsSecureString "Enter a secure master password for the new PostgreSQL Database"
$BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($db_password)
$plain_password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

# 3. Initialize Terraform
Write-Host "Initializing Terraform..." -ForegroundColor Yellow
terraform init

if ($LASTEXITCODE -ne 0) {
    Write-Error "Terraform init failed."
    exit 1
}

# 4. Apply Terraform
Write-Host "Applying Terraform Configuration (This will provision VPC, ALB, ASG, RDS, and ElastiCache)..." -ForegroundColor Yellow
Write-Host "Note: Creating RDS instances can take 5-10 minutes." -ForegroundColor Cyan

terraform apply -auto-approve -var="db_password=$plain_password"

if ($LASTEXITCODE -ne 0) {
    Write-Error "Terraform apply failed."
    exit 1
}

# 5. Extract Outputs
Write-Host "Fetching Infrastructure Endpoints..." -ForegroundColor Yellow
$alb_dns = terraform output -raw alb_dns_name
$rds_endpoint = terraform output -raw rds_endpoint
$redis_endpoint = terraform output -raw redis_endpoint

Write-Host "=========================================" -ForegroundColor Green
Write-Host "INFRASTRUCTURE PROVISIONED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "ALB Frontend URL : http://$alb_dns"
Write-Host "RDS Endpoint     : $rds_endpoint"
Write-Host "Redis Endpoint   : $redis_endpoint"
Write-Host "=========================================" -ForegroundColor Green

Write-Host "IMPORTANT: Update your backend/.env file with these new endpoints." -ForegroundColor Yellow
