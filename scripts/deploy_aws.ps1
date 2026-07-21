# Refresh PATH to load AWS CLI
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

Write-Host "=== Robot Fleet Platform: AWS Automated EC2 Deployer ===" -ForegroundColor Cyan

# 1. Get Latest Ubuntu 22.04 LTS AMI
Write-Host "Searching for latest Ubuntu 22.04 LTS AMI in us-east-1..."
$ami_id = aws ec2 describe-images --owners amazon --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" "Name=state,Values=available" --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text
if (-not $ami_id) {
    Write-Error "Failed to find Ubuntu AMI."
    exit 1
}
Write-Host "Found AMI: $ami_id" -ForegroundColor Green

# 2. Key Pair Setup
Write-Host "Checking for existing AWS Key Pair 'fleet-key'..."
$null = aws ec2 describe-key-pairs --key-names fleet-key --output text 2>$null
$key_exists = ($LASTEXITCODE -eq 0)
$pem_exists = Test-Path "fleet-key.pem"

if ($key_exists -and -not $pem_exists) {
    Write-Host "AWS has 'fleet-key' but local 'fleet-key.pem' is missing. Re-creating key pair..." -ForegroundColor Yellow
    $null = aws ec2 delete-key-pair --key-name fleet-key
    $key_exists = $false
}

if (-not $key_exists -or -not $pem_exists) {
    if (Test-Path "fleet-key.pem") { Remove-Item "fleet-key.pem" }
    Write-Host "Creating new Key Pair 'fleet-key'..."
    $key_material = aws ec2 create-key-pair --key-name fleet-key --query "KeyMaterial" --output text
    if ($key_material) {
        $key_string = $key_material -join "`r`n"
        [System.IO.File]::WriteAllText("fleet-key.pem", $key_string)
        Write-Host "Key pair generated and saved locally to 'fleet-key.pem'" -ForegroundColor Green
    } else {
        Write-Error "Failed to create key pair."
        exit 1
    }
} else {
    Write-Host "Using existing Key Pair 'fleet-key' and local 'fleet-key.pem'." -ForegroundColor Green
}

# 3. Security Group Setup
Write-Host "Checking for existing Security Group 'fleet-security-group'..."
$sg_id = aws ec2 describe-security-groups --group-names fleet-security-group --query "SecurityGroups[0].GroupId" --output text 2>$null
if ($LASTEXITCODE -ne 0 -or -not $sg_id) {
    Write-Host "Creating new Security Group 'fleet-security-group'..."
    $sg_id = aws ec2 create-security-group --group-name fleet-security-group --description "Security group for Robot Fleet Platform" --query "GroupId" --output text
    if ($sg_id) {
        Write-Host "Security Group created: $sg_id" -ForegroundColor Green
        Write-Host "Configuring firewall rules (ports 22, 80, 8000)..."
        aws ec2 authorize-security-group-ingress --group-id $sg_id --protocol tcp --port 22 --cidr 0.0.0.0/0
        aws ec2 authorize-security-group-ingress --group-id $sg_id --protocol tcp --port 80 --cidr 0.0.0.0/0
        aws ec2 authorize-security-group-ingress --group-id $sg_id --protocol tcp --port 8000 --cidr 0.0.0.0/0
        Write-Host "Firewall rules successfully applied." -ForegroundColor Green
    } else {
        Write-Error "Failed to create Security Group."
        exit 1
    }
} else {
    Write-Host "Using existing Security Group: $sg_id" -ForegroundColor Yellow
}

# 4. Launch EC2 Instance
Write-Host "Launching t3.micro EC2 Instance..."
$instance_id = aws ec2 run-instances --image-id $ami_id --instance-type t3.micro --key-name fleet-key --security-group-ids $sg_id --query "Instances[0].InstanceId" --output text
if (-not $instance_id) {
    Write-Error "Failed to launch EC2 instance."
    exit 1
}
Write-Host "Instance created successfully: $instance_id" -ForegroundColor Green

# 5. Fetch Public IP Address
Write-Host "Waiting for Public IP assignment..."
$ip = ""
for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 5
    $ip = aws ec2 describe-instances --instance-ids $instance_id --query "Reservations[0].Instances[0].PublicIpAddress" --output text
    if ($ip -and $ip -ne "None" -and $ip -ne "") {
        break
    }
}

if (-not $ip -or $ip -eq "None") {
    Write-Host "Instance is booting up, but IP assignment is taking longer than expected." -ForegroundColor Yellow
    Write-Host "You can fetch it manually via: aws ec2 describe-instances --instance-ids $instance_id"
} else {
    Write-Host "=========================================" -ForegroundColor Green
    Write-Host "EC2 INSTANCE LAUNCHED SUCCESSFULLY!" -ForegroundColor Green
    Write-Host "Instance ID: $instance_id"
    Write-Host "Public IP:   $ip" -ForegroundColor Yellow
    Write-Host "=========================================" -ForegroundColor Green
    Write-Host "SSH Command to Connect:"
    Write-Host "ssh -i 'fleet-key.pem' ubuntu@$ip" -ForegroundColor Cyan
    Write-Host "=========================================" -ForegroundColor Green
}
