# FleetOps: provisions (or repairs) the single EC2 host the CD pipeline
# deploys to. Safe to re-run: every resource is check-then-create, including
# the instance itself.
#
#   -ForceNewInstance   launch a second host even if a tagged one exists.
param(
    [switch]$ForceNewInstance
)

# Refresh PATH to load AWS CLI
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

Write-Host "=== FleetOps: AWS Automated EC2 Deployer ===" -ForegroundColor Cyan

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
    $sg_id = aws ec2 create-security-group --group-name fleet-security-group --description "Security group for FleetOps" --query "GroupId" --output text
    if ($sg_id) {
        Write-Host "Security Group created: $sg_id" -ForegroundColor Green
        # 22 for the SSH tunnel the Prometheus UI is reached through, 80 for
        # nginx. Not 8000: docker-compose.aws.yml never publishes the backend
        # port -- nginx proxies to it over the internal network -- so opening
        # it exposed nothing but an unauthenticated API if anyone ever did.
        Write-Host "Configuring firewall rules (ports 22, 80)..."
        aws ec2 authorize-security-group-ingress --group-id $sg_id --protocol tcp --port 22 --cidr 0.0.0.0/0
        aws ec2 authorize-security-group-ingress --group-id $sg_id --protocol tcp --port 80 --cidr 0.0.0.0/0
        Write-Host "Firewall rules successfully applied." -ForegroundColor Green
    } else {
        Write-Error "Failed to create Security Group."
        exit 1
    }
} else {
    Write-Host "Using existing Security Group: $sg_id" -ForegroundColor Yellow
    # A group created before this script stopped opening 8000 still has the
    # rule. Reported rather than revoked: docker-compose.yml (the local file)
    # does publish 8000, so a box brought up with it would lose reachability
    # the moment the rule went away. That is the operator's call, not this
    # script's, and it is one command.
    $port_8000 = aws ec2 describe-security-groups --group-ids $sg_id --query "SecurityGroups[0].IpPermissions[?FromPort==``8000``] | length(@)" --output text 2>$null
    if ($port_8000 -and $port_8000 -ne "0") {
        Write-Host "WARNING: this group still opens port 8000 to the internet." -ForegroundColor Red
        Write-Host "  docker-compose.aws.yml never publishes that port -- nginx reaches the" -ForegroundColor Red
        Write-Host "  backend over the internal network -- so it is very likely unused. Close it:" -ForegroundColor Red
        Write-Host "  aws ec2 revoke-security-group-ingress --group-id $sg_id --protocol tcp --port 8000 --cidr 0.0.0.0/0" -ForegroundColor Yellow
    }
}

# 4. SSM Instance Profile
# The deploy-aws job in CI reaches this host with `aws ssm send-command`.
# Without an instance profile granting AmazonSSMManagedInstanceCore the SSM
# agent never registers, the host is invisible to Systems Manager, and the
# deploy silently targets nothing.
$role_name = "fleet-ssm-role"
$profile_name = "fleet-ssm-profile"

Write-Host "Checking for IAM role '$role_name'..."
$null = aws iam get-role --role-name $role_name --output text 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating IAM role '$role_name'..."
    $trust = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    $trust_path = Join-Path $env:TEMP "fleet-ssm-trust.json"
    [System.IO.File]::WriteAllText($trust_path, $trust)
    $null = aws iam create-role --role-name $role_name --assume-role-policy-document "file://$trust_path" --description "Lets the FleetOps app host be managed by SSM"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create IAM role '$role_name'."
        exit 1
    }
    Remove-Item $trust_path -ErrorAction SilentlyContinue
    Write-Host "IAM role created." -ForegroundColor Green
} else {
    Write-Host "Using existing IAM role '$role_name'." -ForegroundColor Yellow
}

# Idempotent: attaching an already-attached policy is a no-op.
$null = aws iam attach-role-policy --role-name $role_name --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

$null = aws iam get-instance-profile --instance-profile-name $profile_name --output text 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating instance profile '$profile_name'..."
    $null = aws iam create-instance-profile --instance-profile-name $profile_name
    $null = aws iam add-role-to-instance-profile --instance-profile-name $profile_name --role-name $role_name
    # IAM is eventually consistent; run-instances rejects a profile it cannot
    # see yet, and there is no waiter for this.
    Write-Host "Waiting 15s for IAM propagation..."
    Start-Sleep -Seconds 15
    Write-Host "Instance profile created." -ForegroundColor Green
} else {
    Write-Host "Using existing instance profile '$profile_name'." -ForegroundColor Yellow
}

# 5. Locate or launch the app host
# Everything above is check-then-create; run-instances was not, so re-running
# this script produced a second t3.micro -- and then the CD job's tag target
# matched two hosts and deployed to both. Reuse the existing one instead, and
# repair whatever it is missing.
$states = "Name=instance-state-name,Values=pending,running,stopping,stopped"

Write-Host "Looking for an existing host tagged role=fleet-app..."
$found = aws ec2 describe-instances --filters "Name=tag:role,Values=fleet-app" $states --query "Reservations[].Instances[].InstanceId" --output text
$instance_id = ($found -split "\s+" | Where-Object { $_ } | Select-Object -First 1)

if (-not $instance_id) {
    # A host from before this script applied tags carries none at all, so the
    # search above cannot see it. The key pair is the only marker it has.
    $legacy = aws ec2 describe-instances --filters "Name=key-name,Values=fleet-key" $states --query "Reservations[].Instances[].InstanceId" --output text
    $legacy_id = ($legacy -split "\s+" | Where-Object { $_ } | Select-Object -First 1)
    if ($legacy_id) {
        Write-Host "Found untagged host $legacy_id launched with 'fleet-key'. Adopting it." -ForegroundColor Yellow
        $null = aws ec2 create-tags --resources $legacy_id --tags "Key=role,Value=fleet-app" "Key=Name,Value=fleetops-app"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to tag $legacy_id. The CD job cannot target it until this succeeds."
            exit 1
        }
        Write-Host "Tagged role=fleet-app." -ForegroundColor Green
        $instance_id = $legacy_id
    }
}

$forced_new = $false
if ($instance_id -and $ForceNewInstance) {
    $forced_new = $true
    Write-Host "-ForceNewInstance given: launching another host alongside $instance_id." -ForegroundColor Yellow
    Write-Host "Both will carry role=fleet-app, so the CD job will deploy to both." -ForegroundColor Yellow
    $instance_id = $null
}

$reused = [bool]$instance_id

if ($reused) {
    Write-Host "Reusing existing instance: $instance_id" -ForegroundColor Green

    # An instance profile can be attached to a running instance, so a host that
    # predates the profile does not have to be rebuilt to become SSM-managed.
    $assoc = aws ec2 describe-iam-instance-profile-associations --filters "Name=instance-id,Values=$instance_id" "Name=state,Values=associating,associated" --query "IamInstanceProfileAssociations[0].IamInstanceProfile.Arn" --output text 2>$null
    if (-not $assoc -or $assoc -eq "None") {
        Write-Host "No instance profile attached. Attaching '$profile_name'..."
        $null = aws ec2 associate-iam-instance-profile --instance-id $instance_id --iam-instance-profile "Name=$profile_name"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to attach the instance profile. SSM cannot reach this host without it."
            exit 1
        }
        Write-Host "Instance profile attached. The SSM agent registers within a few minutes." -ForegroundColor Green
    } else {
        Write-Host "Instance profile already attached: $assoc" -ForegroundColor Green
    }
} else {
    # Tagged role=fleet-app because that tag is the CD job's SSM target, and
    # bootstrapped from user-data because the deploy assumes Docker and a
    # checkout at /opt/fleetops already exist on the box.
    $user_data = Join-Path $PSScriptRoot "ec2_user_data.sh"
    if (-not (Test-Path $user_data)) {
        Write-Error "Missing bootstrap script: $user_data"
        exit 1
    }
    # .Replace, not -replace: the right-hand operand of -replace is a regex,
    # where a lone backslash is not a valid pattern.
    $user_data_uri = "file://" + $user_data.Replace('\', '/')

    if (-not $forced_new) { Write-Host "No existing host found." }
    Write-Host "Launching t3.micro EC2 Instance..."
    $instance_id = aws ec2 run-instances --image-id $ami_id --instance-type t3.micro --key-name fleet-key --security-group-ids $sg_id --iam-instance-profile "Name=$profile_name" --user-data $user_data_uri --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=fleetops-app},{Key=role,Value=fleet-app}]" --query "Instances[0].InstanceId" --output text
    if (-not $instance_id -or $LASTEXITCODE -ne 0) {
        Write-Error "Failed to launch EC2 instance."
        exit 1
    }
    Write-Host "Instance created successfully: $instance_id" -ForegroundColor Green
}

# 6. Fetch Public IP Address
# A reused instance already has one; only a fresh launch needs the wait.
Write-Host "Fetching public IP..."
$ip = ""
$attempts = if ($reused) { 1 } else { 12 }
for ($i = 0; $i -lt $attempts; $i++) {
    if (-not $reused) { Start-Sleep -Seconds 5 }
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
    if ($reused) {
        Write-Host "EXISTING HOST REPAIRED" -ForegroundColor Green
    } else {
        Write-Host "EC2 INSTANCE LAUNCHED SUCCESSFULLY!" -ForegroundColor Green
    }
    Write-Host "Instance ID: $instance_id"
    Write-Host "Public IP:   $ip" -ForegroundColor Yellow
    Write-Host "=========================================" -ForegroundColor Green
    Write-Host "SSH Command to Connect:"
    Write-Host "ssh -i 'fleet-key.pem' ubuntu@$ip" -ForegroundColor Cyan
    Write-Host "=========================================" -ForegroundColor Green
}

# User-data runs once, at first boot. A reused host therefore never sees it,
# and may be missing the Docker install or the /opt/fleetops checkout that the
# rollout acts on. ec2_user_data.sh is written to be idempotent precisely so it
# can be run by hand here.
if ($reused) {
    Write-Host ""
    Write-Host "This host was not bootstrapped by this script. If it lacks Docker or" -ForegroundColor Yellow
    Write-Host "/opt/fleetops, run the bootstrap on it -- it is safe to re-run:" -ForegroundColor Yellow
    Write-Host "  scp -i fleet-key.pem scripts/ec2_user_data.sh ubuntu@${ip}:/tmp/" -ForegroundColor Cyan
    Write-Host "  ssh -i fleet-key.pem ubuntu@$ip 'sudo bash /tmp/ec2_user_data.sh'" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "Before the CD job can deploy to this host:" -ForegroundColor Cyan
Write-Host "  1. Write /opt/fleetops/backend/.env on it (see backend/.env.example)."
Write-Host "  2. 'docker login ghcr.io' on it, if the GHCR packages are private."
Write-Host "  3. Confirm SSM sees it:"
Write-Host "     aws ssm describe-instance-information --filters Key=tag:role,Values=fleet-app" -ForegroundColor Cyan
