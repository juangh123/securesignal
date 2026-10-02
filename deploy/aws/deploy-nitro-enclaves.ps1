[CmdletBinding()]
param(
    [string]$Region = "us-east-1",
    [string]$InstanceType = "m5.xlarge",
    [string]$NamePrefix = "securesignal",
    [string]$ApiCidr = "",
    [string]$KeyName = "",
    [string]$FrontendOrigins = "https://securesignal.vercel.app",
    [string]$RpcUrl = "https://coston2-api.flare.network/ext/C/rpc",
    [string]$LlmBaseUrl = "https://api.deepseek.com/v1",
    [string]$LlmModel = "deepseek-flash",
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\..\tee-service\.env")
)

$ErrorActionPreference = "Stop"

function Invoke-Aws {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & $script:Aws @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "aws failed: aws $($Arguments -join ' ')"
    }
}

function Get-AwsJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $raw = & $script:Aws @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "aws failed: aws $($Arguments -join ' ')"
    }
    return ($raw | ConvertFrom-Json)
}

function Read-EnvFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Env file not found: $Path"
    }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $values[$matches[1]] = $matches[2].Trim()
        }
    }
    return $values
}

function Write-TempFile {
    param([string]$Content)
    $path = Join-Path $env:TEMP "securesignal-$([guid]::NewGuid().ToString('N')).json"
    [System.IO.File]::WriteAllText($path, $Content, [System.Text.UTF8Encoding]::new($false))
    return $path
}

function Set-SecretValue {
    param(
        [string]$SecretId,
        [string]$Body
    )
    $path = Write-TempFile -Content $Body
    try {
        $exists = $true
        & $script:Aws secretsmanager describe-secret --secret-id $SecretId --region $Region *> $null
        if ($LASTEXITCODE -ne 0) {
            $exists = $false
        }
        if ($exists) {
            Invoke-Aws @(
                "secretsmanager", "put-secret-value",
                "--secret-id", $SecretId,
                "--secret-string", "file://$path",
                "--region", $Region
            ) | Out-Null
        }
        else {
            Invoke-Aws @(
                "secretsmanager", "create-secret",
                "--name", $SecretId,
                "--secret-string", "file://$path",
                "--region", $Region
            ) | Out-Null
        }
    }
    finally {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
}

$awsCommand = Get-Command aws.exe -ErrorAction SilentlyContinue
if ($awsCommand) {
    $script:Aws = $awsCommand.Source
}
else {
    $fallback = "$env:ProgramFiles\Amazon\AWSCLIV2\aws.exe"
    if (-not (Test-Path -LiteralPath $fallback)) {
        throw "AWS CLI was not found."
    }
    $script:Aws = $fallback
}

$identity = Get-AwsJson @("sts", "get-caller-identity")
if ($identity.Arn -match ':root$') {
    throw "Refusing to deploy with the AWS root user. Configure IAM Identity Center or a dedicated IAM user."
}
$accountId = $identity.Account
$registry = "$accountId.dkr.ecr.$Region.amazonaws.com"
$repository = "$NamePrefix-tee"
$imageUri = "$registry/$repository`:latest"
$secretId = "$NamePrefix/enclave-env"
$roleName = "$NamePrefix-ec2-role"
$profileName = "$NamePrefix-ec2-profile"
$securityGroupName = "$NamePrefix-api"
$instanceName = "$NamePrefix-tee"

if (-not $ApiCidr) {
    $publicIp = (Invoke-RestMethod -Uri "https://checkip.amazonaws.com" -TimeoutSec 15).Trim()
    $ApiCidr = "$publicIp/32"
}
if ($ApiCidr -notmatch '^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$') {
    throw "ApiCidr must be an IPv4 CIDR such as 203.0.113.10/32"
}

Write-Host "Account: $accountId"
Write-Host "Region:  $Region"
Write-Host "Image:   $imageUri"
Write-Host "API CIDR: $ApiCidr"

& $script:Aws ecr describe-repositories --repository-names $repository --region $Region *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Aws @(
        "ecr", "create-repository",
        "--repository-name", $repository,
        "--image-scanning-configuration", "scanOnPush=true",
        "--region", $Region
    ) | Out-Null
}

$bucket = "$NamePrefix-$accountId-$Region-source"
$sourceKey = "tee-service/$([guid]::NewGuid().ToString('N')).zip"
& $script:Aws s3api head-bucket --bucket $bucket *> $null
if ($LASTEXITCODE -ne 0) {
    $bucketArguments = @("s3api", "create-bucket", "--bucket", $bucket)
    if ($Region -ne "us-east-1") {
        $bucketArguments += @(
            "--create-bucket-configuration", "LocationConstraint=$Region"
        )
    }
    Invoke-Aws $bucketArguments | Out-Null
}

$teeDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\tee-service")).Path
$sourceZip = Join-Path $env:TEMP "securesignal-tee-$([guid]::NewGuid().ToString('N')).zip"
$stageDir = Join-Path $env:TEMP "securesignal-tee-stage-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $stageDir | Out-Null
try {
    foreach ($entry in @(
        "Dockerfile",
        ".dockerignore",
        "requirements.txt",
        "requirements-lock.txt",
        "main.py",
        "aws_rpc_gateway.py",
        "aws_vsock_proxy.py",
        "aws_vsock_rpc_bridge.py",
        "aws_enclave_entrypoint.sh",
        "aws_nitro_bootstrap.py",
        "gcp_secrets_bootstrap.py",
        "analysis",
        "attestation",
        "crypto",
        "flare",
        "config",
        "aws-nsm-helper"
    )) {
        $source = Join-Path $teeDir $entry
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination $stageDir -Recurse -Force
        }
    }
    $helperTarget = Join-Path $stageDir "aws-nsm-helper\target"
    Remove-Item -LiteralPath $helperTarget -Recurse -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path (Join-Path $stageDir "*") -DestinationPath $sourceZip -Force
    Invoke-Aws @(
        "s3", "cp", $sourceZip, "s3://$bucket/$sourceKey",
        "--region", $Region
    ) | Out-Null
}
finally {
    Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $sourceZip -Force -ErrorAction SilentlyContinue
}

$envValues = Read-EnvFile -Path $EnvFile
$bundle = @{
    "ENV" = "prod"
    "ATTESTATION_PROVIDER" = "aws-nitro-enclaves"
    "AWS_NITRO_ENCLAVES" = "1"
    "PORT" = "8000"
    "RPC_URL" = "ipc:///tmp/coston2-rpc.sock"
    "ALLOWED_ORIGINS" = $FrontendOrigins
    "ANALYSIS_OFFLINE" = "0"
    "ANALYZE_REQUIRE_ONCHAIN_TASK" = "1"
    "LLM_BASE_URL" = $LlmBaseUrl
    "LLM_MODEL" = $LlmModel
}
foreach ($key in "TEE_PRIVATE_KEY", "PRIVATE_KEY", "LLM_API_KEY") {
    if ($envValues[$key]) {
        $bundle[$key] = $envValues[$key]
    }
}
if (-not $bundle["TEE_PRIVATE_KEY"] -or -not $bundle["PRIVATE_KEY"]) {
    throw "TEE_PRIVATE_KEY and PRIVATE_KEY are required in $EnvFile"
}
Set-SecretValue -SecretId $secretId -Body ($bundle | ConvertTo-Json -Compress)

$trustPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
'@
$trustPath = Write-TempFile -Content $trustPolicy
try {
    & $script:Aws iam get-role --role-name $roleName *> $null
    if ($LASTEXITCODE -ne 0) {
        Invoke-Aws @(
            "iam", "create-role",
            "--role-name", $roleName,
            "--assume-role-policy-document", "file://$trustPath",
            "--tags", "Key=Project,Value=SecureSignal"
        ) | Out-Null
    }
}
finally {
    Remove-Item -LiteralPath $trustPath -Force -ErrorAction SilentlyContinue
}

Invoke-Aws @(
    "iam", "attach-role-policy",
    "--role-name", $roleName,
    "--policy-arn", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
) | Out-Null

$instancePolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:$Region`:$accountId`:secret:$secretId-*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::$bucket/$sourceKey"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    }
  ]
}
"@
$instancePolicyPath = Write-TempFile -Content $instancePolicy
try {
    Invoke-Aws @(
        "iam", "put-role-policy",
        "--role-name", $roleName,
        "--policy-name", "$NamePrefix-instance-policy",
        "--policy-document", "file://$instancePolicyPath"
    ) | Out-Null
}
finally {
    Remove-Item -LiteralPath $instancePolicyPath -Force -ErrorAction SilentlyContinue
}

& $script:Aws iam get-instance-profile --instance-profile-name $profileName *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Aws @(
        "iam", "create-instance-profile",
        "--instance-profile-name", $profileName
    ) | Out-Null
    Invoke-Aws @(
        "iam", "add-role-to-instance-profile",
        "--instance-profile-name", $profileName,
        "--role-name", $roleName
    ) | Out-Null
    Start-Sleep -Seconds 10
}

$vpcId = (& $script:Aws ec2 describe-vpcs `
    --filters "Name=isDefault,Values=true" `
    --query "Vpcs[0].VpcId" `
    --output text `
    --region $Region).Trim()
if (-not $vpcId -or $vpcId -eq "None") {
    throw "No default VPC exists in $Region. Create one or adapt the deployment script."
}

$supportedZones = @(
    (& $script:Aws ec2 describe-instance-type-offerings `
        --location-type availability-zone `
        --filters "Name=instance-type,Values=$InstanceType" `
        --query "InstanceTypeOfferings[].Location" `
        --output text `
        --region $Region).Trim() -split '\s+'
)
$defaultSubnets = Get-AwsJson @(
    "ec2", "describe-subnets",
    "--filters", "Name=vpc-id,Values=$vpcId", "Name=default-for-az,Values=true",
    "--region", $Region
)
$subnetId = $null
foreach ($subnet in $defaultSubnets.Subnets) {
    if ($supportedZones -contains $subnet.AvailabilityZone) {
        $subnetId = $subnet.SubnetId
        break
    }
}
if (-not $subnetId) {
    throw (
        "No default subnet supports $InstanceType in $Region. " +
        "Create a default subnet in a supported AZ or change -InstanceType."
    )
}

$securityGroupId = (& $script:Aws ec2 describe-security-groups `
    --filters "Name=group-name,Values=$securityGroupName" "Name=vpc-id,Values=$vpcId" `
    --query "SecurityGroups[0].GroupId" `
    --output text `
    --region $Region).Trim()
if (-not $securityGroupId -or $securityGroupId -eq "None") {
    $securityGroupId = (& $script:Aws ec2 create-security-group `
        --group-name $securityGroupName `
        --description "SecureSignal Nitro Enclaves API" `
        --vpc-id $vpcId `
        --query "GroupId" `
        --output text `
        --region $Region).Trim()
}

$ingressRules = @("IpProtocol=tcp,FromPort=8000,ToPort=8000,IpRanges=[{CidrIp=$ApiCidr,Description=SecureSignal API}]")
if ($KeyName) {
    $ingressRules += "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$ApiCidr,Description=SSH}]"
}
$ingressArguments = @(
    "ec2", "authorize-security-group-ingress",
    "--group-id", $securityGroupId,
    "--ip-permissions"
) + $ingressRules + @("--region", $Region)
& $script:Aws @ingressArguments *> $null
# Duplicate rules are fine; AWS reports InvalidPermission.Duplicate.

$amiId = (& $script:Aws ssm get-parameter `
    --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64" `
    --query "Parameter.Value" `
    --output text `
    --region $Region).Trim()

$userDataTemplate = @'
#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/securesignal-bootstrap.log) 2>&1

REGION="__REGION__"
SECRET_ID="__SECRET_ID__"
S3_BUCKET="__S3_BUCKET__"
S3_KEY="__S3_KEY__"
IMAGE_URI="__IMAGE_URI__"
ENCLAVE_CID=16
export NITRO_CLI_ARTIFACTS=/var/nitro_enclaves

dnf update -y
dnf install -y docker jq socat unzip aws-nitro-enclaves-cli aws-nitro-enclaves-cli-devel
systemctl enable --now docker
usermod -aG docker ec2-user

cat >/etc/nitro_enclaves/allocator.yaml <<EOF
---
memory_mib: 3072
cpu_count: 2
EOF
systemctl enable --now nitro-enclaves-allocator
systemctl restart nitro-enclaves-allocator

mkdir -p /opt/securesignal
mkdir -p "$NITRO_CLI_ARTIFACTS"
aws s3 cp "s3://${S3_BUCKET}/${S3_KEY}" /opt/securesignal/source.zip --region "$REGION"
rm -rf /opt/securesignal/src
mkdir -p /opt/securesignal/src
unzip -q /opt/securesignal/source.zip -d /opt/securesignal/src

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${IMAGE_URI%%/*}"
docker build --platform linux/amd64 -t "$IMAGE_URI" /opt/securesignal/src
docker push "$IMAGE_URI"
nitro-cli build-enclave --docker-uri "$IMAGE_URI" --output-file /opt/securesignal/securesignal.eif > /opt/securesignal/build.json

PCR0=$(jq -r '.Measurements.PCR0' /opt/securesignal/build.json)
PCR1=$(jq -r '.Measurements.PCR1' /opt/securesignal/build.json)
PCR2=$(jq -r '.Measurements.PCR2' /opt/securesignal/build.json)

aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" --query SecretString --output text > /opt/securesignal/runtime-env.json
jq --arg pcr0 "$PCR0" --arg pcr1 "$PCR1" --arg pcr2 "$PCR2" \
  '. + {AWS_NITRO_PCR0:$pcr0, AWS_NITRO_PCR1:$pcr1, AWS_NITRO_PCR2:$pcr2}' \
  /opt/securesignal/runtime-env.json > /opt/securesignal/runtime-env.with-pcrs.json
mv /opt/securesignal/runtime-env.with-pcrs.json /opt/securesignal/runtime-env.json
chmod 600 /opt/securesignal/runtime-env.json

cat >/etc/systemd/system/securesignal-secrets.service <<EOF
[Unit]
Description=SecureSignal runtime configuration over vsock
After=network-online.target

[Service]
ExecStart=/usr/bin/socat VSOCK-LISTEN:8001,reuseaddr,fork OPEN:/opt/securesignal/runtime-env.json,rdonly
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/securesignal-rpc.service <<EOF
[Unit]
Description=SecureSignal enclave RPC gateway
After=network-online.target
[Service]
Environment=RPC_UPSTREAM_URL=__RPC_URL__
ExecStart=/usr/bin/python3 /opt/securesignal/src/aws_rpc_gateway.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/securesignal-proxy.service <<EOF
[Unit]
Description=SecureSignal enclave HTTP proxy
After=network-online.target

[Service]
ExecStart=/usr/bin/socat TCP-LISTEN:8000,reuseaddr,fork VSOCK-CONNECT:${ENCLAVE_CID}:8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now securesignal-secrets.service
systemctl enable --now securesignal-rpc.service
nitro-cli terminate-enclave --all || true
nitro-cli run-enclave \
  --enclave-name securesignal-tee \
  --cpu-count 2 \
  --memory 3072 \
  --eif-path /opt/securesignal/securesignal.eif \
  --enclave-cid "$ENCLAVE_CID"
systemctl enable --now securesignal-proxy.service
'@

$userData = $userDataTemplate.
    Replace("__REGION__", $Region).
    Replace("__SECRET_ID__", $secretId).
    Replace("__S3_BUCKET__", $bucket).
    Replace("__S3_KEY__", $sourceKey).
    Replace("__IMAGE_URI__", $imageUri).
    Replace("__RPC_URL__", $RpcUrl)
$userDataPath = Join-Path $env:TEMP "securesignal-user-data-$([guid]::NewGuid().ToString('N')).sh"
[System.IO.File]::WriteAllText($userDataPath, $userData, [System.Text.UTF8Encoding]::new($false))
try {
    $runArguments = @(
        "ec2", "run-instances",
        "--image-id", $amiId,
        "--instance-type", $InstanceType,
        "--iam-instance-profile", "Name=$profileName",
        "--subnet-id", $subnetId,
        "--security-group-ids", $securityGroupId,
        "--metadata-options", "HttpTokens=required,HttpEndpoint=enabled",
        "--enclave-options", "Enabled=true",
        "--user-data", "file://$userDataPath",
        "--tag-specifications", "ResourceType=instance,Tags=[{Key=Name,Value=$instanceName},{Key=Project,Value=SecureSignal}]",
        "--query", "Instances[0].InstanceId",
        "--output", "text",
        "--region", $Region
    )
    if ($KeyName) {
        $runArguments += @("--key-name", $KeyName)
    }
    $instanceId = (& $script:Aws @runArguments).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $instanceId) {
        throw "ec2 run-instances failed."
    }
}
finally {
    Remove-Item -LiteralPath $userDataPath -Force -ErrorAction SilentlyContinue
}

Invoke-Aws @(
    "ec2", "wait", "instance-running",
    "--instance-ids", $instanceId,
    "--region", $Region
) | Out-Null

$publicIp = (& $script:Aws ec2 describe-instances `
    --instance-ids $instanceId `
    --query "Reservations[0].Instances[0].PublicIpAddress" `
    --output text `
    --region $Region).Trim()

Write-Host ""
Write-Host "Nitro Enclaves deployment submitted."
Write-Host "Instance:  $instanceId"
Write-Host "Public IP: $publicIp"
Write-Host "Bootstrap log: /var/log/securesignal-bootstrap.log"
Write-Host "Health: http://$publicIp`:8000/health"
Write-Host ""
Write-Host "The first boot builds the EIF and can take 5-10 minutes."
