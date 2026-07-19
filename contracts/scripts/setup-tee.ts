import { ethers } from "hardhat";
import * as fs from "fs";
import * as path from "path";

/**
 * Stage 2 integration helper: register the dev TEE key on the locally
 * deployed AnalysisRegistry via rotateTeeKey (owner-only).
 *
 * Uses Hardhat account #1 as the TEE key (well-known dev private key),
 * so tee-service can be started with the same key via TEE_PRIVATE_KEY.
 *
 *   Account #1 address: 0x70997970C51812dc3A010C7d01b50e0d17dc79C8
 *   Account #1 privkey: 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
 */
const TEE_PRIVATE_KEY =
  "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";

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
  const imageDigest = ethers.keccak256(ethers.toUtf8Bytes("dev-image"));

  console.log("TEE public key (65B uncompressed):", teePublicKey);
  console.log("TEE address:", teeAddress);
  console.log("Image digest (keccak256('dev-image')):", imageDigest);

  const registry = await ethers.getContractAt(
    "AnalysisRegistry",
    registryAddress,
    deployer
  );

  const tx = await registry.rotateTeeKey(teePublicKey, imageDigest, teeAddress);
  const receipt = await tx.wait();
  console.log("rotateTeeKey tx:", receipt.hash);

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

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
