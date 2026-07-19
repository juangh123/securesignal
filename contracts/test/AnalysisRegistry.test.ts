import { expect } from "chai";
import { ethers } from "hardhat";
import { AnalysisRegistry } from "../typechain-types";
import { HardhatEthersSigner } from "@nomicfoundation/hardhat-ethers/signers";

describe("AnalysisRegistry", function () {
  const imageDigest = ethers.keccak256(ethers.toUtf8Bytes("tee_image_v1"));
  const imageDigestV2 = ethers.keccak256(ethers.toUtf8Bytes("tee_image_v2"));

  let owner: HardhatEthersSigner;
  let teeSigner: HardhatEthersSigner;
  let user: HardhatEthersSigner;
  let attacker: HardhatEthersSigner;
  let registry: AnalysisRegistry;

  // TEE encryption public key: 65-byte uncompressed secp256k1 point (0x04 prefix)
  const teePublicKey =
    "0x04" +
    "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2" +
    "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3";

  beforeEach(async function () {
    [owner, teeSigner, user, attacker] = await ethers.getSigners();
    const factory = await ethers.getContractFactory("AnalysisRegistry");
    registry = await factory.deploy(imageDigest);
    await registry.waitForDeployment();
  });

  /// Builds the attestation exactly as the TEE service does:
  /// EIP-191 personal_sign over the raw 64-byte abi.encodePacked(taskId, resultHash).
  async function signAttestation(
    signer: HardhatEthersSigner,
    taskId: bigint | number,
    resultHash: string
  ): Promise<string> {
    const packed = ethers.solidityPacked(
      ["uint256", "bytes32"],
      [taskId, resultHash]
    );
    return signer.signMessage(ethers.getBytes(packed));
  }

  function rotateAsOwner() {
    return registry
      .connect(owner)
      .rotateTeeKey(teePublicKey, imageDigestV2, teeSigner.address);
  }

  async function requestAsUser(): Promise<bigint> {
    const inputDataHash = ethers.keccak256(
      ethers.toUtf8Bytes("encrypted_input_data")
    );
    const tx = await registry.connect(user).requestAnalysis(inputDataHash);
    const receipt = await tx.wait();
    const event = receipt!.logs
      .map((log) => {
        try {
          return registry.interface.parseLog(log);
        } catch {
          return null;
        }
      })
      .find((e) => e && e.name === "AnalysisRequested");
    return event!.args.taskId as bigint;
  }

  describe("happy path", function () {
    it("registers a task and accepts a correctly TEE-signed result", async function () {
      await rotateAsOwner();

      const inputDataHash = ethers.keccak256(
        ethers.toUtf8Bytes("encrypted_input_data")
      );
      await expect(registry.connect(user).requestAnalysis(inputDataHash))
        .to.emit(registry, "AnalysisRequested")
        .withArgs(0, user.address, inputDataHash);

      const resultHash = ethers.keccak256(
        ethers.toUtf8Bytes('{"pnl":1.23,"price_source":"ftso"}')
      );
      const attestation = await signAttestation(teeSigner, 0, resultHash);

      // Any relayer may submit; only the TEE signature matters
      await expect(
        registry.connect(attacker).submitResult(0, resultHash, attestation)
      )
        .to.emit(registry, "ResultSubmitted")
        .withArgs(0, resultHash);

      const task = await registry.tasks(0);
      expect(task.status).to.equal(3); // Verified
      expect(task.resultHash).to.equal(resultHash);
      expect(task.completedAt).to.be.gt(0);
    });

    it("owner can rotate key and registers teeAddress", async function () {
      await expect(rotateAsOwner())
        .to.emit(registry, "TeeKeyRotated")
        .withArgs(teePublicKey, imageDigestV2, teeSigner.address);

      expect(await registry.teeAddress()).to.equal(teeSigner.address);
      expect(await registry.activeTeePublicKey()).to.equal(teePublicKey);
      expect(await registry.expectedImageDigest()).to.equal(imageDigestV2);
    });
  });

  describe("access control", function () {
    it("reverts when a non-owner calls rotateTeeKey", async function () {
      await expect(
        registry
          .connect(attacker)
          .rotateTeeKey(teePublicKey, imageDigestV2, teeSigner.address)
      ).to.be.revertedWith("Ownable: caller is not the owner");
    });

    it("deployer is the owner", async function () {
      expect(await registry.owner()).to.equal(owner.address);
    });
  });

  describe("attestation verification", function () {
    it("reverts when the attestation was produced by a wrong signer", async function () {
      await rotateAsOwner();
      const taskId = await requestAsUser();
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
      const badAttestation = await signAttestation(attacker, taskId, resultHash);

      await expect(
        registry.submitResult(taskId, resultHash, badAttestation)
      ).to.be.revertedWith("attestation failed");
    });

    it("reverts when attestation signs a different resultHash", async function () {
      await rotateAsOwner();
      const taskId = await requestAsUser();
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
      const otherHash = ethers.keccak256(ethers.toUtf8Bytes("tampered"));
      const attestation = await signAttestation(teeSigner, taskId, otherHash);

      await expect(
        registry.submitResult(taskId, resultHash, attestation)
      ).to.be.revertedWith("attestation failed");
    });

    it("reverts when no teeAddress has been registered", async function () {
      const taskId = await requestAsUser();
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
      const attestation = await signAttestation(teeSigner, taskId, resultHash);

      await expect(
        registry.submitResult(taskId, resultHash, attestation)
      ).to.be.revertedWith("attestation failed");
    });

    it("reverts on a malformed attestation (wrong length)", async function () {
      await rotateAsOwner();
      const taskId = await requestAsUser();
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));

      await expect(
        registry.submitResult(taskId, resultHash, "0x1234")
      ).to.be.revertedWith("attestation failed");
    });
  });

  describe("task status enforcement", function () {
    it("reverts on duplicate submission (task already Verified)", async function () {
      await rotateAsOwner();
      const taskId = await requestAsUser();
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
      const attestation = await signAttestation(teeSigner, taskId, resultHash);

      await registry.submitResult(taskId, resultHash, attestation);

      await expect(
        registry.submitResult(taskId, resultHash, attestation)
      ).to.be.revertedWith("invalid status");
    });

    it("reverts when submitting to a non-existent task", async function () {
      await rotateAsOwner();
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
      const attestation = await signAttestation(teeSigner, 999, resultHash);

      await expect(
        registry.submitResult(999, resultHash, attestation)
      ).to.be.revertedWith("invalid status");
    });

    it("reverts when attestation was made for a different taskId", async function () {
      await rotateAsOwner();
      await requestAsUser(); // task 0
      const taskId1 = await requestAsUser(); // task 1
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
      // TEE signed task 0, but submission targets task 1
      const attestation = await signAttestation(teeSigner, 0, resultHash);

      await expect(
        registry.submitResult(taskId1, resultHash, attestation)
      ).to.be.revertedWith("attestation failed");
    });
  });
});
