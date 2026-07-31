require("dotenv").config();
const { ethers } = require("ethers");
const fs = require("fs");
const path = require("path");

async function main() {
    console.log("=== Node.js Native Deployment ===");
    
    // 1. Setup Provider & Wallet
    const rpcUrl = "https://coston2-api.flare.network/ext/C/rpc";
    const provider = new ethers.JsonRpcProvider(rpcUrl);
    const privateKey = process.env.PRIVATE_KEY;
    if (!privateKey) throw new Error("PRIVATE_KEY not found in .env");
    const wallet = new ethers.Wallet(privateKey, provider);
    
    console.log("Deploying from address:", wallet.address);
    const balance = await provider.getBalance(wallet.address);
    console.log("Balance:", ethers.formatEther(balance), "C2FLR");
    
    // 2. Read compiled artifacts
    const artifactPath = path.join(__dirname, "artifacts", "contracts", "AnalysisRegistry.sol", "AnalysisRegistry.json");
    if (!fs.existsSync(artifactPath)) throw new Error("Contract artifact not found! Run npx hardhat compile first.");
    const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf-8"));
    
    // 3. Deploy AnalysisRegistry
    console.log("\nDeploying AnalysisRegistry...");
    const factory = new ethers.ContractFactory(artifact.abi, artifact.bytecode, wallet);
    // Coston2 FtsoV2 address: 0x3d893C53D9e8A9C67AFAdFdb8B653e020ee3b999
    const ftsoAddress = "0x3d893C53D9e8A9C67AFAdFdb8B653e020ee3b999";
    const registry = await factory.deploy(ftsoAddress);
    await registry.waitForDeployment();
    const registryAddress = await registry.getAddress();
    console.log(">> AnalysisRegistry deployed to:", registryAddress);
    
    // 4. Read mock FtsoReader artifact
    const mockupPath = path.join(__dirname, "artifacts", "contracts", "FtsoV2Reader.sol", "FtsoV2Reader.json");
    const mockupArtifact = JSON.parse(fs.readFileSync(mockupPath, "utf-8"));
    console.log("\nDeploying dummy FtsoV2Reader (to comply with UI frontend configs)...");
    const mockupFactory = new ethers.ContractFactory(mockupArtifact.abi, mockupArtifact.bytecode, wallet);
    const mockup = await mockupFactory.deploy();
    await mockup.waitForDeployment();
    const mockupAddress = await mockup.getAddress();
    console.log(">> FtsoV2Reader deployed to:", mockupAddress);
    
    // 5. Write to config files
    const addresses = {
        AnalysisRegistry: registryAddress,
        FtsoV2Reader: mockupAddress
    };
    
    const frontendPath = path.join(__dirname, "..", "frontend", "src", "config", "contract-addresses.json");
    fs.writeFileSync(frontendPath, JSON.stringify(addresses, null, 2));
    console.log("\nUpdated frontend config:", frontendPath);
    
    const teePath = path.join(__dirname, "..", "tee-service", "config", "contract-addresses.json");
    if (fs.existsSync(path.dirname(teePath))) {
        fs.writeFileSync(teePath, JSON.stringify(addresses, null, 2));
        console.log("Updated tee-service config:", teePath);
    }
    
    // 6. Register TEE Key
    console.log("\nRegistering TEE Key...");
    const teeKey = process.env.TEE_PRIVATE_KEY;
    if (teeKey) {
        const teePublicKey = ethers.SigningKey.computePublicKey(teeKey, false);
        const teeAddress = new ethers.Wallet(teeKey).address;
        const imageDigest = ethers.keccak256(ethers.toUtf8Bytes("dev-image"));
        console.log("Sending rotateTeeKey transaction...");
        const tx = await registry.rotateTeeKey(teePublicKey, imageDigest, teeAddress);
        await tx.wait();
        console.log(">> rotateTeeKey tx hash:", tx.hash);
        console.log("TEE successfully registered to the contract on Coston2!");
    }
}
main().catch(console.error);
