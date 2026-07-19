import { artifacts } from "hardhat";
import * as fs from "fs";
import * as path from "path";

/**
 * Refreshes the AnalysisRegistry ABI/artifact in the frontend and tee-service
 * config directories from the latest compilation output.
 *
 * Run after `npx hardhat compile`. This script writes ONLY the two
 * AnalysisRegistry.json files — contract addresses are written by deploy.ts.
 */
async function main() {
  const artifact = await artifacts.readArtifact("AnalysisRegistry");

  const targets = [
    path.join(__dirname, "..", "..", "frontend", "src", "config"),
    path.join(__dirname, "..", "..", "tee-service", "config"),
  ];

  for (const dir of targets) {
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, "AnalysisRegistry.json");
    fs.writeFileSync(file, JSON.stringify(artifact, null, 2) + "\n");
    console.log(`Wrote ${file}`);
  }

  console.log("AnalysisRegistry ABI exported successfully.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
