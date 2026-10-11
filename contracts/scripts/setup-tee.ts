import { ethers } from "hardhat";
import * as fs from "fs";
import * as path from "path";

/**
 * Register the TEE key on the deployed AnalysisRegistry via the two-step
 * rotation flow (owner-only): proposeTeeKey() then commitTeeKeyRotation().
 * First-time registration is exempt from the rotation delay; re-registering a
 * key on a production registry only activates after teeRotationDelay seconds.
 *
 * - localhost: uses Hardhat account #1 as the TEE key (well-known dev
 *   private key), so tee-service can be started with the same key.
 * - other networks (e.g. coston2): reads the TEE key from env
 *   TEE_PRIVATE_KEY (never hardcode a production key here), and an optional
 *   TEE_IMAGE_DIGEST (bytes32 hex; defaults to keccak256("dev-image")).
 *
 *   Account #1 address: 0x70997970C51812dc3A010C7d01b50e0d17dc79C8
 *   Account #1 privkey: 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
 */
import { network } from "hardhat";

const DEV_TEE_PRIVATE_KEY =
  "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";

// Delayed rotations need two runs: propose once, wait for executableAt, then
// commit with this flag. Re-running the plain script would propose again and
// restart the delay window.
const COMMIT_ONLY = (process.env.SETUP_TEE_COMMIT_ONLY ?? "").trim() === "1";

const TEE_PRIVATE_KEY =
  network.name === "localhost" || network.name === "hardhat"
    ? DEV_TEE_PRIVATE_KEY
    : (() => {
        const k = process.env.TEE_PRIVATE_KEY;
        if (!k || !/^0x[0-9a-fA-F]{64}$/.test(k))
          throw new Error(
            `network ${network.name}: set TEE_PRIVATE_KEY (0x + 64 hex) in env`
          );
        return k;
      })();

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Owner (deployer, account #0):", deployer.address);

  const addressesPath = path.join(
    __dirname,
    "..",
    "..",
    "tee-service",
    "config",
    "contract-addresses.json"
  );
  const addresses = JSON.parse(fs.readFileSync(addressesPath, "utf-8"));
  const registryAddress: string = addresses.AnalysisRegistry;
  if (!registryAddress) throw new Error("AnalysisRegistry address missing");
  console.log("AnalysisRegistry:", registryAddress);

  // 65-byte uncompressed public key (0x04 prefix) of the TEE private key
  const teePublicKey = SigningKey_computeUncompressed(TEE_PRIVATE_KEY);
  const teeAddress = new ethers.Wallet(TEE_PRIVATE_KEY).address;
  const imageDigest = process.env.TEE_IMAGE_DIGEST
    ? normalizeImageDigest(process.env.TEE_IMAGE_DIGEST)
    : ethers.keccak256(ethers.toUtf8Bytes("dev-image"));

  console.log("TEE public key (65B uncompressed):", teePublicKey);
  console.log("TEE address:", teeAddress);
  console.log("Image digest:", imageDigest);

  const registry = await ethers.getContractAt(
    "AnalysisRegistry",
    registryAddress,
    deployer
  );

  if (COMMIT_ONLY) {
    console.log(
      "SETUP_TEE_COMMIT_ONLY=1: skipping proposeTeeKey and committing the pending rotation."
    );
  } else {
    const proposeTx = await registry.proposeTeeKey(
      teePublicKey,
      imageDigest,
      teeAddress
    );
    const proposeReceipt = await proposeTx.wait();
    if (!proposeReceipt) {
      throw new Error("proposeTeeKey transaction was not mined");
    }
    console.log("proposeTeeKey tx:", proposeReceipt.hash);
  }

  try {
    const commitTx = await registry.commitTeeKeyRotation();
    const commitReceipt = await commitTx.wait();
    if (!commitReceipt) {
      throw new Error("commitTeeKeyRotation transaction was not mined");
    }
    console.log("commitTeeKeyRotation tx:", commitReceipt.hash);
  } catch (error) {
    const pending = await registry.pendingTeeKey();
    const pendingExists = Boolean(pending.exists);
    const guidance = pendingExists
      ? `A rotation is pending until executableAt=${pending.executableAt}; wait ` +
        "until then and re-run with SETUP_TEE_COMMIT_ONLY=1 (running the plain " +
        "script again would propose anew and restart the delay)."
      : "No rotation is pending; run the script without SETUP_TEE_COMMIT_ONLY " +
        "to propose one first.";
    throw new Error(
      `commitTeeKeyRotation failed (${(error as Error).message}). ${guidance}`
    );
  }

  // Read back on-chain state for verification
  const onchainPub: string = await registry.activeTeePublicKey();
  const onchainDigest: string = await registry.expectedImageDigest();
  const onchainTee: string = await registry.teeAddress();
  console.log("On-chain activeTeePublicKey:", onchainPub);
  console.log("On-chain expectedImageDigest:", onchainDigest);
  console.log("On-chain teeAddress:", onchainTee);

  if (onchainPub.toLowerCase() !== teePublicKey.toLowerCase())
    throw new Error("on-chain public key mismatch");
  if (onchainDigest.toLowerCase() !== imageDigest.toLowerCase())
    throw new Error("on-chain image digest mismatch");
  if (onchainTee.toLowerCase() !== teeAddress.toLowerCase())
    throw new Error("on-chain tee address mismatch");
  console.log("TEE key registration verified on-chain. OK.");
}

function SigningKey_computeUncompressed(privKey: string): string {
  // ethers v6: SigningKey.computePublicKey(key, false) -> 0x04 || X || Y
  const { SigningKey } = require("ethers") as typeof import("ethers");
  return SigningKey.computePublicKey(privKey, false);
}

function normalizeImageDigest(raw: string | undefined): string {
  const value = (raw ?? "").trim();
  if (/^0x[0-9a-fA-F]{64}$/.test(value)) {
    return value.toLowerCase();
  }
  if (/^sha256:[0-9a-fA-F]{64}$/.test(value)) {
    return `0x${value.slice("sha256:".length).toLowerCase()}`;
  }
  throw new Error(
    "TEE_IMAGE_DIGEST must be either sha256:<64 hex> or 0x<64 hex>"
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
