import { HardhatUserConfig } from "hardhat/config";
import baseConfig from "./hardhat.config";

/**
 * docker-compose variant of hardhat.config.ts.
 *
 * Identical to the base config except the "localhost" network points at the
 * compose `hardhat` service DNS name instead of 127.0.0.1, so the compose
 * `deploy` init service can reach the chain container:
 *
 *   npx hardhat run scripts/deploy.ts   --config hardhat.compose.config.ts --network localhost
 *   npx hardhat run scripts/setup-tee.ts --config hardhat.compose.config.ts --network localhost
 *
 * Not used outside docker-compose.
 */
const config: HardhatUserConfig = {
  ...baseConfig,
  networks: {
    ...baseConfig.networks,
    localhost: {
      url: "http://hardhat:8545",
    },
  },
};

export default config;
