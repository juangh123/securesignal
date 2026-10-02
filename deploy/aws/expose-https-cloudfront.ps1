[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstanceId,

    [string]$Region = "us-east-1",
    [string]$SecurityGroupId = "",
    [string]$OriginDomain = "",
    [string]$Comment = "SecureSignal Nitro Enclave HTTPS",
    [int]$OriginPort = 8000,
    [switch]$SkipWaiting
)

$ErrorActionPreference = "Stop"

function Invoke-Aws {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & $script:Aws @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "aws failed: aws $($Arguments -join ' ')"
    }
}

function Write-JsonFile {
    param([string]$Content)
    $path = Join-Path $env:TEMP "securesignal-cloudfront-$([guid]::NewGuid().ToString('N')).json"
    [System.IO.File]::WriteAllText($path, $Content, [System.Text.UTF8Encoding]::new($false))
    return $path
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

$instance = (& $script:Aws ec2 describe-instances `
    --instance-ids $InstanceId `
    --region $Region `
    --query "Reservations[0].Instances[0]" `
    --output json | ConvertFrom-Json)
if (-not $instance) {
    throw "Instance not found: $InstanceId"
}

if (-not $OriginDomain) {
    $OriginDomain = $instance.PublicDnsName
}
if (-not $OriginDomain) {
    $ip = $instance.PublicIpAddress
    if (-not $ip) {
        throw "Instance has no public DNS name or public IP."
    }
    $OriginDomain = "ec2-$($ip.Replace('.', '-')).compute-1.amazonaws.com"
}

$existing = (& $script:Aws cloudfront list-distributions `
    --query "DistributionList.Items[?Comment=='$Comment'].{Id:Id,DomainName:DomainName,Status:Status}" `
    --output json | ConvertFrom-Json)

if ($existing.Count -gt 0) {
    $distribution = $existing[0]
    Write-Host "Reusing CloudFront distribution $($distribution.Id)"
}
else {
    $config = [ordered]@{
        CallerReference = "securesignal-$InstanceId-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
        Comment = $Comment
        Enabled = $true
        Origins = [ordered]@{
            Quantity = 1
            Items = @(
                [ordered]@{
                    Id = "securesignal-ec2"
                    DomainName = $OriginDomain
                    CustomOriginConfig = [ordered]@{
                        HTTPPort = $OriginPort
                        HTTPSPort = 443
                        OriginProtocolPolicy = "http-only"
                        OriginSslProtocols = [ordered]@{
                            Quantity = 1
                            Items = @("TLSv1.2")
                        }
                        OriginReadTimeout = 30
                        OriginKeepaliveTimeout = 5
                    }
                }
            )
        }
        DefaultCacheBehavior = [ordered]@{
            TargetOriginId = "securesignal-ec2"
            ViewerProtocolPolicy = "redirect-to-https"
            AllowedMethods = [ordered]@{
                Quantity = 7
                Items = @("GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE")
                CachedMethods = [ordered]@{
                    Quantity = 2
                    Items = @("GET", "HEAD")
                }
            }
            ForwardedValues = [ordered]@{
                QueryString = $true
                Cookies = [ordered]@{ Forward = "all" }
                Headers = [ordered]@{
                    Quantity = 1
                    Items = @("*")
                }
            }
            MinTTL = 0
            DefaultTTL = 0
            MaxTTL = 0
            Compress = $false
            TrustedSigners = [ordered]@{
                Enabled = $false
                Quantity = 0
            }
            LambdaFunctionAssociations = [ordered]@{ Quantity = 0 }
        }
        PriceClass = "PriceClass_100"
        Restrictions = [ordered]@{
            GeoRestriction = [ordered]@{
                RestrictionType = "none"
                Quantity = 0
            }
        }
        HttpVersion = "http2and3"
        IsIPV6Enabled = $true
    }
    $configPath = Write-JsonFile -Content ($config | ConvertTo-Json -Depth 12 -Compress)
    try {
        $distribution = (& $script:Aws cloudfront create-distribution `
            --distribution-config "file://$configPath" `
            --query "Distribution.{Id:Id,DomainName:DomainName,Status:Status}" `
            --output json | ConvertFrom-Json)
    }
    finally {
        Remove-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Created CloudFront distribution $($distribution.Id)"
}

if (-not $SkipWaiting) {
    Write-Host "Waiting for CloudFront distribution to deploy..."
    Invoke-Aws @(
        "cloudfront", "wait", "distribution-deployed",
        "--id", $distribution.Id
    ) | Out-Null
}

if (-not $SecurityGroupId) {
    $SecurityGroupId = $instance.SecurityGroups[0].GroupId
}
$prefixListId = (& $script:Aws ec2 describe-managed-prefix-lists `
    --filters "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing" `
    --region $Region `
    --query "PrefixLists[0].PrefixListId" `
    --output text).Trim()
if ($prefixListId -and $prefixListId -ne "None") {
    $permissions = (& $script:Aws ec2 describe-security-groups `
        --group-ids $SecurityGroupId `
        --region $Region `
        --query "SecurityGroups[0].IpPermissions" `
        --output json | ConvertFrom-Json)
    $alreadyAllowed = $false
    foreach ($permission in @($permissions)) {
        if ($null -eq $permission.FromPort -or $null -eq $permission.ToPort) {
            continue
        }
        if (
            $permission.IpProtocol -eq "tcp" -and
            [int]$permission.FromPort -le $OriginPort -and
            [int]$permission.ToPort -ge $OriginPort
        ) {
            foreach ($entry in @($permission.PrefixListIds)) {
                if ($entry.PrefixListId -eq $prefixListId) {
                    $alreadyAllowed = $true
                }
            }
        }
    }
    if ($alreadyAllowed) {
        Write-Host "Security group already allows CloudFront origin-facing access on port $OriginPort"
    }
    else {
        & $script:Aws ec2 authorize-security-group-ingress `
            --group-id $SecurityGroupId `
            --ip-permissions "IpProtocol=tcp,FromPort=$OriginPort,ToPort=$OriginPort,PrefixListIds=[{PrefixListId=$prefixListId,Description=CloudFront-origin-facing}]" `
            --region $Region
        if ($LASTEXITCODE -ne 0) {
            throw "failed to authorize the CloudFront origin-facing prefix list ($prefixListId) on security group $SecurityGroupId"
        }
        Write-Host "Added CloudFront origin-facing access on port $OriginPort"
    }
}
else {
    Write-Warning "CloudFront origin-facing prefix list was not found in $Region; the distribution will not be able to reach the origin."
}

Write-Host ""
Write-Host "CloudFront HTTPS endpoint: https://$($distribution.DomainName)"
Write-Host "Origin:                    http://$OriginDomain`:$OriginPort"
Write-Host "Security group:            $SecurityGroupId"
Write-Host "CloudFront prefix list:    $prefixListId"
