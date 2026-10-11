import { ethers, network } from "hardhat";
import * as fs from "fs";
import * as path from "path";

// FlareContractRegistry — same address on all Flare networks (Coston2 / Flare).
// See https://dev.flare.network/network/solidity-reference/
const FLARE_CONTRACT_REGISTRY = "0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019";

const REGISTRY_ABI = [
  "function getContractAddressByName(string name) external view returns (address)",
];

const SUPPORTED_NETWORKS = ["localhost", "coston2"] as const;
type SupportedNetwork = (typeof SUPPORTED_NETWORKS)[number];

/**
 * Resolves the on-chain FtsoV2 address through the FlareContractRegistry.
 * On localhost there is no registry, so a zero-address placeholder is used
 * (with a loud warning) — price reads must be mocked in local dev.
 */
async function resolveFtsoV2Address(networkName: SupportedNetwork): Promise<string> {
  if (networkName === "coston2") {
    const registry = new ethers.Contract(
      FLARE_CONTRACT_REGISTRY,
      REGISTRY_ABI,
      ethers.provider
    );
    const ftsoV2Address: string = await registry.getContractAddressByName("FtsoV2");
    if (!ftsoV2Address || ftsoV2Address === ethers.ZeroAddress) {
      throw new Error(
        `FtsoV2 not found in FlareContractRegistry (${FLARE_CONTRACT_REGISTRY}) on ${networkName}`
      );
    }
    console.log(`FtsoV2 resolved via FlareContractRegistry: ${ftsoV2Address}`);
    return ftsoV2Address;
  }

  // localhost
  console.warn(
    "WARNING: localhost network — using zero address as FtsoV2 placeholder. " +
      "FtsoV2Reader will revert on price reads; mock FTSO in local dev."
  );
  return ethers.ZeroAddress;
}

function writeAddressConfig(networkName: string, addresses: Record<string, string>) {
  const payload = {
    network: networkName,
    ...addresses,
  };

  // Only these two JSON files are written outside contracts/ — nothing else
  // in frontend/ or tee-service/ is touched by this script.
  const targets = [
    path.join(__dirname, "..", "..", "frontend", "src", "config"),
    path.join(__dirname, "..", "..", "tee-service", "config"),
  ];

  for (const dir of targets) {
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, "contract-addresses.json");
    fs.writeFileSync(file, JSON.stringify(payload, null, 2) + "\n");
    console.log(`Wrote ${file}`);
  }
}

/**
 * Rotation delay passed to the constructor. Public networks default to 48h so
 * a key swap is announced on-chain before it can sign anything (first-time
 * registration is exempt inside the contract); localhost rotates instantly.
 * Override with TEE_ROTATION_DELAY_SECONDS (integer seconds, max 365 days).
 */
function readRotationDelay(networkName: SupportedNetwork): bigint {
  const raw = (process.env.TEE_ROTATION_DELAY_SECONDS ?? "").trim();
  if (raw) {
    if (!/^\d+$/.test(raw)) {
      throw new Error("TEE_ROTATION_DELAY_SECONDS must be an integer number of seconds");
    }
    const value = BigInt(raw);
    if (value > 365n * 24n * 60n * 60n) {
      throw new Error("TEE_ROTATION_DELAY_SECONDS must not exceed 365 days");
    }
    if (
      value === 0n &&
      networkName !== "localhost" &&
      (process.env.ALLOW_ZERO_TEE_ROTATION_DELAY ?? "").trim() !== "1"
    ) {
      throw new Error(
        "TEE_ROTATION_DELAY_SECONDS=0 would disable the key-rotation timelock on " +
          "a public network. Set a positive delay, or set " +
          "ALLOW_ZERO_TEE_ROTATION_DELAY=1 to override deliberately."
      );
    }
    return value;
  }
  return networkName === "localhost" ? 0n : 48n * 60n * 60n;
}

async function main() {
  const networkName = network.name as SupportedNetwork;
  if (!SUPPORTED_NETWORKS.includes(networkName)) {
    throw new Error(
      `Unsupported network "${networkName}". Use --network localhost or --network coston2.`
    );
  }

  const [deployer] = await ethers.getSigners();
  if (!deployer) {
    throw new Error(
      `No signer available on network "${networkName}". ` +
        "For coston2 set PRIVATE_KEY in contracts/.env; " +
        "for localhost start the node first (npx hardhat node)."
    );
  }
  console.log(`Network: ${networkName} (chainId ${network.config.chainId ?? "unknown"})`);
  console.log("Deploying contracts with the account:", deployer.address);

  // Resolve FtsoV2 before deploying anything so we fail fast on coston2
  const ftsoV2Address = await resolveFtsoV2Address(networkName);

  // Deploy FtsoV2Reader
  const FtsoV2Reader = await ethers.getContractFactory("FtsoV2Reader");
  const ftsoV2Reader = await FtsoV2Reader.deploy(ftsoV2Address);
  await ftsoV2Reader.waitForDeployment();
  const ftsoV2ReaderAddress = await ftsoV2Reader.getAddress();
  console.log("FtsoV2Reader deployed to:", ftsoV2ReaderAddress);

  // Deploy AnalysisRegistry. Initial image digest is a placeholder; the owner
  // must proposeTeeKey(pubkey, imageDigest, teeAddress) and then call
  // commitTeeKeyRotation() to register the real TEE before results can be
  // submitted (first registration skips the rotation delay).
  const initialImageDigest = ethers.ZeroHash;
  const rotationDelay = readRotationDelay(networkName);
  console.log(
    `TEE rotation delay: ${rotationDelay}s` +
      (rotationDelay > 0n ? " (first registration is exempt)" : "")
  );
  const AnalysisRegistry = await ethers.getContractFactory("AnalysisRegistry");
  const analysisRegistry = await AnalysisRegistry.deploy(
    initialImageDigest,
    rotationDelay
  );
  await analysisRegistry.waitForDeployment();
  const analysisRegistryAddress = await analysisRegistry.getAddress();
  console.log("AnalysisRegistry deployed to:", analysisRegistryAddress);

  writeAddressConfig(networkName, {
    AnalysisRegistry: analysisRegistryAddress,
    FtsoV2Reader: ftsoV2ReaderAddress,
  });

  console.log("Deployment complete.");
  console.log(
    "NEXT STEP: call proposeTeeKey(teePublicKey, imageDigest, teeAddress) then " +
      "commitTeeKeyRotation() as owner to register the TEE signing key."
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
