[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Pcr0,

    [Parameter(Mandatory = $true)]
    [string]$Pcr1,

    [Parameter(Mandatory = $true)]
    [string]$Pcr2,

    [string]$Region = "us-east-1",
    [string]$NamePrefix = "securesignal",
    [string]$InstanceId = "",
    [string]$Bucket = "",
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

function Get-AwsText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & $script:Aws @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "aws failed: aws $($Arguments -join ' ')"
    }
    return (($output | Out-String).Trim())
}

function Write-TempFile {
    param([string]$Content)
    $path = Join-Path $env:TEMP "securesignal-$([guid]::NewGuid().ToString('N')).json"
    [System.IO.File]::WriteAllText(
        $path,
        $Content,
        [System.Text.UTF8Encoding]::new($false)
    )
    return $path
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

$identity = (
    Get-AwsText @("sts", "get-caller-identity", "--output", "json") |
        ConvertFrom-Json
)
if ($identity.Arn -match ':root$') {
    throw "Refusing to provision KMS with the AWS root user."
}
$accountId = $identity.Account
if (-not $Bucket) {
    $Bucket = "$NamePrefix-$accountId-$Region-source"
}
$aliasName = "alias/$NamePrefix-tee-key"
$bundleKey = "tee-service/$NamePrefix-kms-bundle.json"
$secretId = "$NamePrefix/enclave-env"
$roleArn = "arn:aws:iam::$accountId`:role/$NamePrefix-ec2-role"

$keyId = Get-AwsText @(
    "kms", "list-aliases",
    "--query", "Aliases[?AliasName=='$aliasName'].TargetKeyId",
    "--output", "text",
    "--region", $Region
)
if (-not $keyId -or $keyId -eq "None") {
    $keyId = $null
}

$keyPolicy = [ordered]@{
    Version = "2012-10-17"
    Statement = @(
        [ordered]@{
            Sid = "KeyAdministration"
            Effect = "Allow"
            Principal = @{ AWS = $identity.Arn }
            Action = "kms:*"
            Resource = "*"
        },
        [ordered]@{
            Sid = "AllowDeployEncrypt"
            Effect = "Allow"
            Principal = @{ AWS = $identity.Arn }
            Action = @("kms:Encrypt", "kms:DescribeKey")
            Resource = "*"
        },
        [ordered]@{
            Sid = "AllowEnclaveDecrypt"
            Effect = "Allow"
            Principal = @{ AWS = $roleArn }
            Action = @("kms:Decrypt", "kms:DescribeKey")
            Resource = "*"
            Condition = @{
                StringEquals = @{
                    "kms:RecipientAttestation:PCR0" = @(
                        $Pcr0.ToLower(), $Pcr0.ToUpper()
                    )
                    "kms:RecipientAttestation:PCR1" = @(
                        $Pcr1.ToLower(), $Pcr1.ToUpper()
                    )
                    "kms:RecipientAttestation:PCR2" = @(
                        $Pcr2.ToLower(), $Pcr2.ToUpper()
                    )
                }
            }
        }
    )
}
$policyPath = Write-TempFile -Content ($keyPolicy | ConvertTo-Json -Depth 12)
try {
    if (-not $keyId) {
        $keyId = Get-AwsText @(
            "kms", "create-key",
            "--policy", "file://$policyPath",
            "--description", "SecureSignal TEE key release ($NamePrefix)",
            "--key-usage", "ENCRYPT_DECRYPT",
            "--origin", "AWS_KMS",
            "--tags", "TagKey=Project,TagValue=SecureSignal",
            "--query", "KeyMetadata.KeyId",
            "--output", "text",
            "--region", $Region
        )
        if ($LASTEXITCODE -ne 0 -or -not $keyId) {
            throw "kms create-key failed."
        }
        Invoke-Aws @(
            "kms", "create-alias",
            "--alias-name", $aliasName,
            "--target-key-id", $keyId,
            "--region", $Region
        ) | Out-Null
    }
    else {
        Invoke-Aws @(
            "kms", "put-key-policy",
            "--key-id", $keyId,
            "--policy-name", "default",
            "--policy", "file://$policyPath",
            "--region", $Region
        ) | Out-Null
    }
}
finally {
    Remove-Item -LiteralPath $policyPath -Force -ErrorAction SilentlyContinue
}

$keyArn = "arn:aws:kms:$Region`:$accountId`:key/$keyId"
& $script:Aws kms enable-key-rotation --key-id $keyId --region $Region *> $null

$values = Read-EnvFile -Path $EnvFile
foreach ($required in "TEE_PRIVATE_KEY", "PRIVATE_KEY") {
    if (-not $values[$required]) {
        throw "$required is required in $EnvFile"
    }
}

function Get-KmsCiphertext {
    param([string]$Plaintext)
    $plaintextPath = Join-Path $env:TEMP "securesignal-kms-plaintext-$([guid]::NewGuid().ToString('N')).txt"
    [System.IO.File]::WriteAllText(
        $plaintextPath,
        $Plaintext,
        [System.Text.UTF8Encoding]::new($false)
    )
    try {
        $ciphertext = Get-AwsText @(
            "kms", "encrypt",
            "--key-id", $keyArn,
            "--plaintext", "fileb://$plaintextPath",
            "--encryption-context", "app=securesignal,purpose=tee-key-release",
            "--query", "CiphertextBlob",
            "--output", "text",
            "--region", $Region
        )
        if ($LASTEXITCODE -ne 0 -or -not $ciphertext) {
            throw "kms encrypt failed."
        }
        return $ciphertext
    }
    finally {
        Remove-Item -LiteralPath $plaintextPath -Force -ErrorAction SilentlyContinue
    }
}

$sealed = [ordered]@{
    ENV = "prod"
    ATTESTATION_PROVIDER = "aws-nitro-enclaves"
    AWS_NITRO_ENCLAVES = "1"
    PORT = "8000"
    RPC_URL = "ipc:///tmp/coston2-rpc.sock"
    ALLOWED_ORIGINS = "https://securesignal.vercel.app,http://localhost:3000"
    ANALYSIS_OFFLINE = "0"
    ANALYZE_REQUIRE_ONCHAIN_TASK = "1"
    LLM_BASE_URL = "https://api.deepseek.com/v1"
    LLM_MODEL = "deepseek-flash"
    KMS_KEY_RELEASE = "1"
    KMS_KEY_ID = $keyArn
    KMS_REGION = $Region
    KMS_VSOCK_PORT = "8600"
    AWS_NITRO_PCR0 = $Pcr0
    AWS_NITRO_PCR1 = $Pcr1
    AWS_NITRO_PCR2 = $Pcr2
    TEE_PRIVATE_KEY_CIPHERTEXT = Get-KmsCiphertext -Plaintext $values["TEE_PRIVATE_KEY"]
    PRIVATE_KEY_CIPHERTEXT = Get-KmsCiphertext -Plaintext $values["PRIVATE_KEY"]
}
if ($values["LLM_API_KEY"]) {
    $sealed["LLM_API_KEY_CIPHERTEXT"] = Get-KmsCiphertext -Plaintext $values["LLM_API_KEY"]
}

$bundlePath = Write-TempFile -Content ($sealed | ConvertTo-Json -Compress)
try {
    Invoke-Aws @(
        "s3", "cp", $bundlePath, "s3://$Bucket/$bundleKey",
        "--region", $Region
    ) | Out-Null
    Set-SecretValue -SecretId $secretId -Body ($sealed | ConvertTo-Json -Compress)
}
finally {
    Remove-Item -LiteralPath $bundlePath -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "KMS key release provisioned."
Write-Host "KMS key:      $keyArn"
Write-Host "Sealed bundle: s3://$Bucket/$bundleKey"
if ($InstanceId) {
    Write-Host "Instance:     $InstanceId"
}
