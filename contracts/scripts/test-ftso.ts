import { ethers } from "hardhat";

async function main() {
  console.log("Starting FTSO v2 Price Read Test on Coston2...");

  // FlareContractRegistry — same address on all Flare networks (Coston2 / Flare).
  // See https://dev.flare.network/network/solidity-reference/
  const registryAddress = "0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019";
  const provider = new ethers.JsonRpcProvider("https://coston2-api.flare.network/ext/C/rpc");
  
  try {
    const registryAbi = [
      "function getContractAddressByName(string name) external view returns (address)"
    ];
    const registry = new ethers.Contract(registryAddress, registryAbi, provider);

    // Resolve FtsoV2 through the canonical FlareContractRegistry
    const ftsoV2Address = await registry.getContractAddressByName("FtsoV2");
    console.log("FtsoV2 Address on Coston2:", ftsoV2Address);

    if (ftsoV2Address === ethers.ZeroAddress || ftsoV2Address === "0x") {
      console.error("FtsoV2 not found in registry!");
      return;
    }

    const ftsoV2Abi = [
      "function getFeedById(bytes21 _feedId) external view returns (uint256 value, int8 decimals, uint64 timestamp)"
    ];
    const ftsoV2 = new ethers.Contract(ftsoV2Address, ftsoV2Abi, provider);

    const feedId = "0x014254432f55534400000000000000000000000000"; // BTC/USD

    console.log(`Fetching price for feedId ${feedId}...`);
    const [value, decimals, timestamp] = await ftsoV2.getFeedById(feedId);

    console.log(`\nPrice Result for BTC/USD:`);
    console.log(`Value: ${value.toString()}`);
    console.log(`Decimals: ${decimals}`);
    console.log(`Timestamp: ${timestamp}`);
    
    const actualPrice = Number(value) / (10 ** Number(decimals));
    console.log(`Actual Price: $${actualPrice}`);

    console.log("\nFTSO V2 Fetch Test: SUCCESS");

  } catch (error) {
    console.error("Error reading price:");
    console.error(error);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});