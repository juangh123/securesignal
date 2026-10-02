[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region = "us-central1",
    [string]$Zone = "us-central1-a",
    [string]$InstanceName = "securesignal-tee",
    [string]$MachineType = "n2d-standard-2",
    [string]$RepoName = "securesignal",
    [string]$ImageName = "tee",
    [string]$ServiceAccountName = "securesignal-tee",
    [string]$Tag = (Get-Date -Format "yyyyMMdd-HHmmss"),
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\..\tee-service\.env"),
    [string]$FrontendOrigins = "https://securesignal.vercel.app",
    [string]$RpcUrl = "https://coston2-api.flare.network/ext/C/rpc",
    [string]$LlmBaseUrl = "https://api.deepseek.com/v1",
    [string]$LlmModel = "deepseek-flash",
    [string]$AttestationAudience = "https://securesignal.app",
    [switch]$SkipSecrets
)

$ErrorActionPreference = "Stop"

function Resolve-Gcloud {
    $command = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $fallback = "C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (Test-Path -LiteralPath $fallback) {
        return $fallback
    }
    throw "gcloud.cmd was not found. Install Google Cloud SDK and reopen the terminal."
}

function Invoke-Gcloud {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )
    & $script:Gcloud @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud failed: gcloud $($Arguments -join ' ')"
    }
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

function Set-GcpSecret {
    param(
        [string]$Name,
        [string]$Value
    )

    & $script:Gcloud secrets describe $Name --project $ProjectId --format="value(name)" *> $null
    if ($LASTEXITCODE -ne 0) {
        $Value | & $script:Gcloud secrets create $Name `
            --project $ProjectId `
            --replication-policy=automatic `
            --data-file=-
        if ($LASTEXITCODE -ne 0) {
            throw "failed to create secret $Name"
        }
    }
    else {
        $Value | & $script:Gcloud secrets versions add $Name `
            --project $ProjectId `
            --data-file=-
        if ($LASTEXITCODE -ne 0) {
            throw "failed to add a version to secret $Name"
        }
    }
}

$script:Gcloud = Resolve-Gcloud
$activeAccount = (& $script:Gcloud auth list --filter=status:ACTIVE --format="value(account)").Trim()
if (-not $activeAccount) {
    throw "No active gcloud account. Run: gcloud auth login"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$teeDir = Join-Path $repoRoot "tee-service"
$envValues = Read-EnvFile -Path $EnvFile
$image = "$Region-docker.pkg.dev/$ProjectId/$RepoName/$ImageName`:$Tag"
$serviceAccountEmail = "$ServiceAccountName@$ProjectId.iam.gserviceaccount.com"
$firewallRule = "$InstanceName-allow-8000"
$firewallTag = $InstanceName

Write-Host "Project: $ProjectId"
Write-Host "Account: $activeAccount"
Write-Host "Image:   $image"

$billingEnabled = (& $script:Gcloud billing projects describe $ProjectId `
    --format="value(billingEnabled)").Trim()
if ($LASTEXITCODE -ne 0 -or $billingEnabled -ne "True") {
    throw (
        "Billing is not enabled for $ProjectId. Open " +
        "https://console.cloud.google.com/billing/linkedaccount?project=$ProjectId " +
        "and link a billing account before deploying Confidential Space."
    )
}

Invoke-Gcloud @(
    "services", "enable",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "compute.googleapis.com",
    "confidentialcomputing.googleapis.com",
    "iamcredentials.googleapis.com",
    "secretmanager.googleapis.com",
    "--project", $ProjectId
)

& $script:Gcloud artifacts repositories describe $RepoName `
    --location $Region `
    --project $ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud @(
        "artifacts", "repositories", "create", $RepoName,
        "--repository-format=docker",
        "--location=$Region",
        "--project=$ProjectId"
    )
}

& $script:Gcloud iam service-accounts describe $serviceAccountEmail `
    --project $ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud @(
        "iam", "service-accounts", "create", $ServiceAccountName,
        "--display-name=SecureSignal Confidential Space workload",
        "--project=$ProjectId"
    )
}

$projectNumber = (& $script:Gcloud projects describe $ProjectId `
    --format="value(projectNumber)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $projectNumber) {
    throw "Could not resolve project number for $ProjectId"
}
$cloudBuildServiceAccount = "$projectNumber@cloudbuild.gserviceaccount.com"

Invoke-Gcloud @(
    "projects", "add-iam-policy-binding", $ProjectId,
    "--member=serviceAccount:$cloudBuildServiceAccount",
    "--role=roles/artifactregistry.writer",
    "--condition=None",
    "--quiet"
)
Invoke-Gcloud @(
    "projects", "add-iam-policy-binding", $ProjectId,
    "--member=serviceAccount:$serviceAccountEmail",
    "--role=roles/confidentialcomputing.workloadUser",
    "--condition=None",
    "--quiet"
)
Invoke-Gcloud @(
    "projects", "add-iam-policy-binding", $ProjectId,
    "--member=serviceAccount:$serviceAccountEmail",
    "--role=roles/logging.logWriter",
    "--condition=None",
    "--quiet"
)

Invoke-Gcloud @(
    "artifacts", "repositories", "add-iam-policy-binding", $RepoName,
    "--location=$Region",
    "--project=$ProjectId",
    "--member=serviceAccount:$serviceAccountEmail",
    "--role=roles/artifactregistry.reader"
)

$secretMappings = @{}
if (-not $SkipSecrets) {
    $teePrivateKey = [string]$envValues["TEE_PRIVATE_KEY"]
    $relayerPrivateKey = [string]$envValues["PRIVATE_KEY"]
    $llmApiKey = [string]$envValues["LLM_API_KEY"]

    if (-not $teePrivateKey) {
        throw "TEE_PRIVATE_KEY is missing from $EnvFile"
    }
    if (-not $relayerPrivateKey) {
        throw "PRIVATE_KEY is missing from $EnvFile"
    }

    Set-GcpSecret -Name "securesignal-tee-private-key" -Value $teePrivateKey
    Set-GcpSecret -Name "securesignal-relayer-private-key" -Value $relayerPrivateKey
    $secretMappings["GCP_SECRET_TEE_PRIVATE_KEY"] = "securesignal-tee-private-key"
    $secretMappings["GCP_SECRET_PRIVATE_KEY"] = "securesignal-relayer-private-key"

    if ($llmApiKey) {
        Set-GcpSecret -Name "securesignal-llm-api-key" -Value $llmApiKey
        $secretMappings["GCP_SECRET_LLM_API_KEY"] = "securesignal-llm-api-key"
    }

    foreach ($secretName in $secretMappings.Values) {
        Invoke-Gcloud @(
            "secrets", "add-iam-policy-binding", $secretName,
            "--project=$ProjectId",
            "--member=serviceAccount:$serviceAccountEmail",
            "--role=roles/secretmanager.secretAccessor",
            "--quiet"
        )
    }
}

Invoke-Gcloud @(
    "builds", "submit", $teeDir,
    "--tag=$image",
    "--project=$ProjectId"
)

$digest = (& $script:Gcloud artifacts docker images describe $image `
    --project $ProjectId `
    --format="value(image_summary.digest)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $digest.StartsWith("sha256:")) {
    throw "Could not resolve the built image digest for $image"
}
Write-Host "Image digest: $digest"

$metadataEntries = @(
    "tee-image-reference=$image@$digest",
    "tee-container-log-redirect=true",
    "tee-restart-policy=Always",
    "tee-env-ENV=prod",
    "tee-env-GCP_SECRETS_ENABLED=1",
    "tee-env-GOOGLE_CLOUD_PROJECT=$ProjectId",
    "tee-env-TEE_IMAGE_DIGEST=$digest",
    "tee-env-GCP_ATTESTATION_AUDIENCE=$AttestationAudience",
    "tee-env-RPC_URL=$RpcUrl",
    "tee-env-ALLOWED_ORIGINS=$FrontendOrigins",
    "tee-env-ANALYSIS_OFFLINE=0",
    "tee-env-ANALYZE_REQUIRE_ONCHAIN_TASK=1",
    "tee-env-LLM_BASE_URL=$LlmBaseUrl",
    "tee-env-LLM_MODEL=$LlmModel"
) + @($secretMappings.GetEnumerator() | ForEach-Object {
    "tee-env-$($_.Key)=$($_.Value)"
})
$metadata = "^~^" + ($metadataEntries -join "~")

$instanceExists = $false
& $script:Gcloud compute instances describe $InstanceName `
    --zone $Zone `
    --project $ProjectId *> $null
if ($LASTEXITCODE -eq 0) {
    $instanceExists = $true
}

if ($instanceExists) {
    Write-Host "Updating existing Confidential Space VM: $InstanceName"
    Invoke-Gcloud @(
        "compute", "instances", "stop", $InstanceName,
        "--zone=$Zone", "--project=$ProjectId", "--quiet"
    )
    Invoke-Gcloud @(
        "compute", "instances", "update", $InstanceName,
        "--zone=$Zone",
        "--project=$ProjectId",
        "--metadata=$metadata"
    )
    Invoke-Gcloud @(
        "compute", "instances", "start", $InstanceName,
        "--zone=$Zone", "--project=$ProjectId", "--quiet"
    )
}
else {
    Write-Host "Creating Confidential Space VM: $InstanceName"
    Invoke-Gcloud @(
        "compute", "instances", "create", $InstanceName,
        "--zone=$Zone",
        "--project=$ProjectId",
        "--machine-type=$MachineType",
        "--confidential-compute-type=SEV",
        "--min-cpu-platform=AMD Milan",
        "--maintenance-policy=MIGRATE",
        "--shielded-secure-boot",
        "--image-project=confidential-space-images",
        "--image-family=confidential-space",
        "--boot-disk-size=20GB",
        "--service-account=$serviceAccountEmail",
        "--scopes=cloud-platform",
        "--tags=$firewallTag",
        "--metadata=$metadata"
    )
}

& $script:Gcloud compute firewall-rules describe $firewallRule `
    --project $ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud @(
        "compute", "firewall-rules", "create", $firewallRule,
        "--project=$ProjectId",
        "--allow=tcp:8000",
        "--target-tags=$firewallTag",
        "--source-ranges=0.0.0.0/0",
        "--description=SecureSignal Confidential Space API"
    )
}

$publicIp = (& $script:Gcloud compute instances describe $InstanceName `
    --zone $Zone `
    --project $ProjectId `
    --format="value(networkInterfaces[0].accessConfigs[0].natIP)").Trim()

Write-Host ""
Write-Host "Deployment submitted."
Write-Host "VM:         $InstanceName ($Zone)"
Write-Host "Public IP:  $publicIp"
Write-Host "Health:     http://$publicIp`:8000/health"
Write-Host "Attestation audience: $AttestationAudience"
Write-Host "Image digest: $digest"
Write-Host ""
Write-Host "After the service is healthy, register the key on Coston2 with:"
Write-Host "  `$env:TEE_IMAGE_DIGEST='$digest'"
Write-Host "  `$env:TEE_PRIVATE_KEY=(read from deploy environment)"
Write-Host "  cd contracts; npx hardhat run scripts/setup-tee.ts --network coston2"
