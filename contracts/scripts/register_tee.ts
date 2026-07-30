import { ethers } from "hardhat";
import fs from "fs";
import path from "path";

async function main() {
    // Check for hardhat runtime
    const hre = (typeof (window as any) !== "undefined") ? null : require("hardhat");
    const network = hre ? hre.network.name : process.env.HARDHAT_NETWORK;
    const configPath = path.join(__dirname, "../../frontend/src/config/contract-addresses.json");
    if (!fs.existsSync(configPath)) {
        console.error("Contract addresses not found!");
        return;
    }
    const config = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    const registryAddress = config.AnalysisRegistry;

    const [deployer] = await ethers.getSigners();
    const registry = await ethers.getContractAt("AnalysisRegistry", registryAddress);

    try {
        const fetch = (await import('node-fetch')).default || global.fetch;
        const res = await fetch("http://127.0.0.1:3000/public-key");
        if (res.ok) {
             const data = await res.json();
             const publicKey = data.public_key.startsWith('04') ? '0x' + data.public_key.slice(2) : (data.public_key.startsWith('0x') ? data.public_key : "0x" + data.public_key);
             const address = data.address || data.tee_address;
             
             console.log(`Registering TEE address: ${address}`);
             const tx = await registry.rotateTeeKey(publicKey, "0x0000000000000000000000000000000000000000000000000000000000000000", address);
            await tx.wait();
            console.log("Registered successfully.");
        } else {
             console.log(`Failed to fetch from logic: ${res.status} ${res.statusText}`);
        }
    } catch(e: any) {
        console.log(`Could not register tee key automatically right now: ${e.message}`);
    }
}

main().catch(console.error);