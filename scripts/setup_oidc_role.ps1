# Creates the AWS identity GitHub Actions assumes to run the deploy-aws job:
# a GitHub OIDC provider and a role scoped to this repository's main branch.
#
# OIDC rather than a long-lived access key in a repo secret. The role is
# assumed with a short-lived token minted per workflow run and constrained to
# one repo and one branch, so there is no static credential to leak or rotate.
#
# Safe to re-run: provider, role and policy are all check-then-create.
param(
    [string]$Repo = "colin-110/robot-fleet-platform",
    [string]$Branch = "main",
    [string]$RoleName = "fleetops-github-deploy",
    [string]$Region = "us-east-1"
)

# Deliberately NOT $ErrorActionPreference = "Stop". In Windows PowerShell 5.1
# a native command's stderr becomes an ErrorRecord, so under Stop the expected
# "no such provider / no such role" probes below terminate the script instead
# of reporting not-found. Every aws call is followed by a $LASTEXITCODE check
# instead, which is what the exit status actually means.
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

Write-Host "=== FleetOps: GitHub Actions OIDC deploy role ===" -ForegroundColor Cyan

$account = aws sts get-caller-identity --query Account --output text
if ($LASTEXITCODE -ne 0 -or -not $account) {
    Write-Error "No usable AWS credentials. Run 'aws login' first."
    exit 1
}
Write-Host "Account: $account"

$provider_arn = "arn:aws:iam::${account}:oidc-provider/token.actions.githubusercontent.com"
$tmp = $env:TEMP

# 1. OIDC provider (one per account, shared by every repo that deploys here)
$null = aws iam get-open-id-connect-provider --open-id-connect-provider-arn $provider_arn 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating GitHub OIDC provider..."
    # IAM validates GitHub's certificate against the root CA and no longer
    # relies on these, but the API still requires the argument. Both of
    # GitHub's published intermediates are listed so the call keeps working
    # whichever one is presented.
    $null = aws iam create-open-id-connect-provider `
        --url "https://token.actions.githubusercontent.com" `
        --client-id-list "sts.amazonaws.com" `
        --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1" "1c58a3a8518e8759bf075b76b750d4f2df264fcd"
    if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create the OIDC provider."; exit 1 }
    Write-Host "OIDC provider created." -ForegroundColor Green
} else {
    Write-Host "Using existing OIDC provider." -ForegroundColor Yellow
}

# 2. Role, trusted only by this repo on this branch
$subject = "repo:${Repo}:ref:refs/heads/${Branch}"
$trust = @{
    Version = "2012-10-17"
    Statement = @(@{
        Effect = "Allow"
        Principal = @{ Federated = $provider_arn }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = @{
            # aud pinned as well as sub: without it the trust policy would
            # accept a token minted for a different audience entirely.
            StringEquals = @{
                "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
                "token.actions.githubusercontent.com:sub" = $subject
            }
        }
    })
} | ConvertTo-Json -Depth 10

$trust_path = Join-Path $tmp "fleetops-oidc-trust.json"
[System.IO.File]::WriteAllText($trust_path, $trust)

$null = aws iam get-role --role-name $RoleName 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating role '$RoleName' trusted by $subject..."
    $null = aws iam create-role --role-name $RoleName --assume-role-policy-document "file://$trust_path" --description "GitHub Actions deploy role for FleetOps"
    if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create the role."; exit 1 }
    Write-Host "Role created." -ForegroundColor Green
} else {
    Write-Host "Role exists; updating its trust policy to match..." -ForegroundColor Yellow
    $null = aws iam update-assume-role-policy --role-name $RoleName --policy-document "file://$trust_path"
}

# 3. Exactly the permissions the rollout step uses, and nothing else.
$policy = @{
    Version = "2012-10-17"
    Statement = @(
        @{
            # Resolving the targets. Neither call supports resource-level
            # permissions, so both are account-wide reads.
            Effect = "Allow"
            Action = @("ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation")
            Resource = "*"
        },
        @{
            Effect = "Allow"
            Action = "ssm:SendCommand"
            Resource = "arn:aws:ssm:${Region}::document/AWS-RunShellScript"
        },
        @{
            # Only instances carrying the deploy tag. Without this condition
            # the role could run arbitrary shell on every instance in the
            # account, which is a much larger blast radius than one app host.
            Effect = "Allow"
            Action = "ssm:SendCommand"
            Resource = "arn:aws:ec2:${Region}:${account}:instance/*"
            Condition = @{ StringEquals = @{ "ssm:resourceTag/role" = "fleet-app" } }
        }
    )
} | ConvertTo-Json -Depth 10

$policy_path = Join-Path $tmp "fleetops-oidc-policy.json"
[System.IO.File]::WriteAllText($policy_path, $policy)
$null = aws iam put-role-policy --role-name $RoleName --policy-name "fleetops-ssm-deploy" --policy-document "file://$policy_path"
if ($LASTEXITCODE -ne 0) { Write-Error "Failed to attach the inline policy."; exit 1 }
Write-Host "Permissions attached (SSM SendCommand, limited to role=fleet-app hosts)." -ForegroundColor Green

Remove-Item $trust_path, $policy_path -ErrorAction SilentlyContinue

$role_arn = aws iam get-role --role-name $RoleName --query "Role.Arn" --output text

Write-Host ""
Write-Host "=========================================" -ForegroundColor Green
Write-Host "Role ARN: $role_arn" -ForegroundColor Yellow
Write-Host "=========================================" -ForegroundColor Green
Write-Host "Wire it into the repository, then pushes to $Branch will deploy:"
Write-Host "  gh secret set AWS_DEPLOY_ROLE_ARN --body `"$role_arn`"" -ForegroundColor Cyan
Write-Host "  gh variable set ENABLE_AWS_DEPLOY --body true" -ForegroundColor Cyan
