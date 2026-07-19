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

async function main() {
  const networkName = network.name as SupportedNetwork;
  if (!SUPPORTED_NETWORKS.includes(networkName)) {
    throw new Error(
      `Unsupported network "${networkName}". Use --network localhost or --network coston2.`
    );
  }

  const [deployer] = await ethers.getSigners();
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

  // Deploy AnalysisRegistry. Initial image digest is a placeholder;
  // the owner must call rotateTeeKey(pubkey, imageDigest, teeAddress)
  // to register the real TEE before results can be submitted.
  const initialImageDigest = ethers.ZeroHash;
  const AnalysisRegistry = await ethers.getContractFactory("AnalysisRegistry");
  const analysisRegistry = await AnalysisRegistry.deploy(initialImageDigest);
  await analysisRegistry.waitForDeployment();
  const analysisRegistryAddress = await analysisRegistry.getAddress();
  console.log("AnalysisRegistry deployed to:", analysisRegistryAddress);

  writeAddressConfig(networkName, {
    AnalysisRegistry: analysisRegistryAddress,
    FtsoV2Reader: ftsoV2ReaderAddress,
  });

  console.log("Deployment complete.");
  console.log(
    "NEXT STEP: call rotateTeeKey(teePublicKey, imageDigest, teeAddress) as owner " +
      "to register the TEE signing key."
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
