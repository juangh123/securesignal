import { expect } from "chai";
import { time } from "@nomicfoundation/hardhat-network-helpers";
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
  let newTee: HardhatEthersSigner;
  let registry: AnalysisRegistry;

  // TEE encryption public key: 65-byte uncompressed secp256k1 point (0x04 prefix)
  const teePublicKey =
    "0x04" +
    "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2" +
    "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3";

  // EIP-712 type definition, kept literal so drifting either the domain or the
  // struct shape breaks the signature tests loudly.
  const ATTESTATION_TYPES = {
    Attestation: [
      { name: "taskId", type: "uint256" },
      { name: "resultHash", type: "bytes32" },
    ],
  };

  beforeEach(async function () {
    [owner, teeSigner, user, attacker, newTee] = await ethers.getSigners();
    const factory = await ethers.getContractFactory("AnalysisRegistry");
    // Delay 0 keeps the fixture fast; the delayed flow is covered explicitly
    // in the "key rotation timelock" describe block below.
    registry = await factory.deploy(imageDigest, 0);
    await registry.waitForDeployment();
  });

  /// Builds the attestation exactly as the TEE service does for a v2 registry:
  /// EIP-712 typed data over Attestation(uint256 taskId, bytes32 resultHash)
  /// with the domain {name: "SecureSignal", version: "2", chainId,
  /// verifyingContract}. The verifying contract is read from the deployed
  /// registry (or the caller-provided one) on every call, because each test
  /// deploys a fresh registry and the domain is bound to its address.
  async function signAttestation(
    signer: HardhatEthersSigner,
    taskId: bigint | number,
    resultHash: string,
    verifyingContract?: string
  ): Promise<string> {
    const { chainId } = await ethers.provider.getNetwork();
    const domain = {
      name: "SecureSignal",
      version: "2",
      chainId,
      verifyingContract: verifyingContract ?? (await registry.getAddress()),
    };
    return signer.signTypedData(domain, ATTESTATION_TYPES, { taskId, resultHash });
  }

  async function rotateAsOwner() {
    await registry
      .connect(owner)
      .proposeTeeKey(teePublicKey, imageDigestV2, teeSigner.address);
    return registry.connect(owner).commitTeeKeyRotation();
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
      // The format enforced by proposeTeeKey starts here: 65 bytes, 0x04 prefix.
      expect(ethers.getBytes(teePublicKey).length).to.equal(65);

      await expect(rotateAsOwner())
        .to.emit(registry, "TeeKeyRotated")
        .withArgs(teePublicKey, imageDigestV2, teeSigner.address);

      expect(await registry.teeAddress()).to.equal(teeSigner.address);
      expect(await registry.activeTeePublicKey()).to.equal(teePublicKey);
      expect(await registry.expectedImageDigest()).to.equal(imageDigestV2);
    });
  });

  describe("access control", function () {
    it("reverts when a non-owner proposes a TEE key", async function () {
      await expect(
        registry
          .connect(attacker)
          .proposeTeeKey(teePublicKey, imageDigestV2, teeSigner.address)
      ).to.be.revertedWith("Ownable: caller is not the owner");
    });

    it("reverts when a non-owner commits or cancels a rotation", async function () {
      await registry
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, teeSigner.address);

      await expect(
        registry.connect(attacker).commitTeeKeyRotation()
      ).to.be.revertedWith("Ownable: caller is not the owner");
      await expect(
        registry.connect(attacker).cancelTeeKeyRotation()
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

  describe("attestation domain separation", function () {
    it("rejects a signature produced for another registry deployment", async function () {
      await rotateAsOwner(); // registry A, deployed in beforeEach

      const factory = await ethers.getContractFactory("AnalysisRegistry");
      const registryB = await factory.deploy(imageDigest, 0);
      await registryB.waitForDeployment();
      await registryB
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, teeSigner.address);
      await registryB.connect(owner).commitTeeKeyRotation();

      const taskId = await requestAsUser(); // task 0 on registry A
      const inputDataHash = ethers.keccak256(
        ethers.toUtf8Bytes("encrypted_input_data")
      );
      await registryB.connect(user).requestAnalysis(inputDataHash); // task 0 on B

      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
      const attestationForB = await signAttestation(
        teeSigner,
        taskId,
        resultHash,
        await registryB.getAddress()
      );

      // Same TEE key, same chain, same (taskId, resultHash): only the domain
      // (verifyingContract) differs, so replay against A must fail.
      await expect(
        registry.submitResult(taskId, resultHash, attestationForB)
      ).to.be.revertedWith("attestation failed");

      const attestationForA = await signAttestation(teeSigner, taskId, resultHash);
      await expect(
        registryB.submitResult(taskId, resultHash, attestationForA)
      ).to.be.revertedWith("attestation failed");

      // The B signature itself is valid, it simply belongs to registry B.
      await expect(registryB.submitResult(taskId, resultHash, attestationForB))
        .to.emit(registryB, "ResultSubmitted")
        .withArgs(taskId, resultHash);
    });
  });

  describe("proposeTeeKey validation", function () {
    it("rejects a public key that is not 65 bytes", async function () {
      const shortKey = teePublicKey.slice(0, -2); // drop one byte
      await expect(
        registry
          .connect(owner)
          .proposeTeeKey(shortKey, imageDigestV2, teeSigner.address)
      ).to.be.revertedWith("public key must be 65 bytes");
    });

    it("rejects a 65-byte key that is not an uncompressed point", async function () {
      const compressed = "0x02" + teePublicKey.slice(4); // 65 bytes, 0x02 prefix
      await expect(
        registry
          .connect(owner)
          .proposeTeeKey(compressed, imageDigestV2, teeSigner.address)
      ).to.be.revertedWith("public key must be uncompressed (0x04)");
    });

    it("rejects a zero image digest", async function () {
      await expect(
        registry
          .connect(owner)
          .proposeTeeKey(teePublicKey, ethers.ZeroHash, teeSigner.address)
      ).to.be.revertedWith("zero image digest");
    });

    it("rejects the zero tee address", async function () {
      await expect(
        registry
          .connect(owner)
          .proposeTeeKey(teePublicKey, imageDigestV2, ethers.ZeroAddress)
      ).to.be.revertedWith("zero tee address");
    });
  });

  describe("key rotation", function () {
    it("invalidates the old TEE signature and accepts the new key", async function () {
      await rotateAsOwner(); // teeSigner is the active TEE

      const taskId = await requestAsUser();
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));

      await registry
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, newTee.address);
      await registry.connect(owner).commitTeeKeyRotation();
      expect(await registry.teeAddress()).to.equal(newTee.address);

      const oldKeyAttestation = await signAttestation(teeSigner, taskId, resultHash);
      await expect(
        registry.submitResult(taskId, resultHash, oldKeyAttestation)
      ).to.be.revertedWith("attestation failed");

      const newKeyAttestation = await signAttestation(newTee, taskId, resultHash);
      await expect(registry.submitResult(taskId, resultHash, newKeyAttestation))
        .to.emit(registry, "ResultSubmitted")
        .withArgs(taskId, resultHash);
    });
  });

  describe("key rotation timelock", function () {
    const ROTATION_DELAY = 48n * 60n * 60n; // 48h

    async function deployWithDelay(delay: bigint): Promise<AnalysisRegistry> {
      const factory = await ethers.getContractFactory("AnalysisRegistry");
      const deployed = await factory.deploy(imageDigest, delay);
      await deployed.waitForDeployment();
      return deployed;
    }

    it("allows the first registration immediately even with a delay set", async function () {
      const delayed = await deployWithDelay(ROTATION_DELAY);

      await delayed
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, teeSigner.address);
      await expect(delayed.connect(owner).commitTeeKeyRotation())
        .to.emit(delayed, "TeeKeyRotated")
        .withArgs(teePublicKey, imageDigestV2, teeSigner.address);

      expect(await delayed.teeAddress()).to.equal(teeSigner.address);
      expect(await delayed.teeRotationDelay()).to.equal(ROTATION_DELAY);
      expect((await delayed.pendingTeeKey()).exists).to.equal(false);
    });

    it("announces a rotation and refuses to activate it before the delay", async function () {
      const delayed = await deployWithDelay(ROTATION_DELAY);
      await delayed
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, teeSigner.address);
      await delayed.connect(owner).commitTeeKeyRotation();

      await expect(
        delayed
          .connect(owner)
          .proposeTeeKey(teePublicKey, imageDigestV2, newTee.address)
      ).to.emit(delayed, "TeeKeyRotationProposed");

      const pending = await delayed.pendingTeeKey();
      expect(pending.exists).to.equal(true);
      expect(pending.newTeeAddress).to.equal(newTee.address);
      expect(pending.executableAt).to.be.greaterThan(await time.latest());

      await expect(
        delayed.connect(owner).commitTeeKeyRotation()
      ).to.be.revertedWith("rotation delay not elapsed");
      // The active key is untouched while the rotation is pending.
      expect(await delayed.teeAddress()).to.equal(teeSigner.address);

      await time.increase(ROTATION_DELAY + 1n);
      await expect(delayed.connect(owner).commitTeeKeyRotation())
        .to.emit(delayed, "TeeKeyRotated")
        .withArgs(teePublicKey, imageDigestV2, newTee.address);

      expect(await delayed.teeAddress()).to.equal(newTee.address);
      expect((await delayed.pendingTeeKey()).exists).to.equal(false);
    });

    it("cancel clears the announcement and commit then reverts", async function () {
      const delayed = await deployWithDelay(ROTATION_DELAY);
      await delayed
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, teeSigner.address);
      await delayed.connect(owner).commitTeeKeyRotation();

      await delayed
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, newTee.address);
      await expect(delayed.connect(owner).cancelTeeKeyRotation())
        .to.emit(delayed, "TeeKeyRotationCancelled")
        .withArgs(teePublicKey, imageDigestV2, newTee.address);

      expect((await delayed.pendingTeeKey()).exists).to.equal(false);
      expect(await delayed.teeAddress()).to.equal(teeSigner.address);
      await expect(
        delayed.connect(owner).commitTeeKeyRotation()
      ).to.be.revertedWith("no pending rotation");
    });

    it("re-proposing replaces the pending entry and restarts the delay", async function () {
      const delayed = await deployWithDelay(ROTATION_DELAY);
      await delayed
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, teeSigner.address);
      await delayed.connect(owner).commitTeeKeyRotation();

      await delayed
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, newTee.address);
      const first = await delayed.pendingTeeKey();

      await time.increase(60);
      await delayed
        .connect(owner)
        .proposeTeeKey(teePublicKey, imageDigestV2, user.address);
      const second = await delayed.pendingTeeKey();

      expect(second.newTeeAddress).to.equal(user.address);
      expect(second.executableAt).to.be.greaterThan(first.executableAt);
    });

    it("commit without a pending proposal reverts", async function () {
      await expect(
        registry.connect(owner).commitTeeKeyRotation()
      ).to.be.revertedWith("no pending rotation");
      await expect(
        registry.connect(owner).cancelTeeKeyRotation()
      ).to.be.revertedWith("no pending rotation");
    });

    it("rejects a delay longer than one year", async function () {
      const factory = await ethers.getContractFactory("AnalysisRegistry");
      await expect(
        factory.deploy(imageDigest, 366n * 24n * 60n * 60n)
      ).to.be.revertedWith("rotation delay too large");
    });
  });

  describe("signature malleability", function () {
    it("rejects a signature whose v byte is out of range", async function () {
      await rotateAsOwner();
      const taskId = await requestAsUser();
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
      const attestation = await signAttestation(teeSigner, taskId, resultHash);
      const { r, s } = ethers.Signature.from(attestation);

      for (const badV of [0x00, 0x1f]) {
        const malformed = ethers.concat([r, s, new Uint8Array([badV])]);
        await expect(
          registry.submitResult(taskId, resultHash, malformed)
        ).to.be.revertedWith("attestation failed");
      }

      // The untouched signature still verifies, so the rejections above come
      // from the v byte rather than from a broken fixture.
      await expect(registry.submitResult(taskId, resultHash, attestation))
        .to.emit(registry, "ResultSubmitted")
        .withArgs(taskId, resultHash);
    });

    it("rejects a high-s variant of an otherwise valid signature", async function () {
      await rotateAsOwner();
      const taskId = await requestAsUser();
      const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
      const attestation = await signAttestation(teeSigner, taskId, resultHash);
      const { r, s, v } = ethers.Signature.from(attestation);

      const secp256k1n =
        BigInt("0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141");
      const highS = ethers.toBeHex(secp256k1n - BigInt(s), 32);
      const flippedV = v === 27 ? 28 : 27;
      const malleated = ethers.concat([r, highS, new Uint8Array([flippedV])]);

      // OpenZeppelin's ECDSA rejects s > n/2 without recovering a signer, so
      // the contract maps the failure to "attestation failed".
      await expect(
        registry.submitResult(taskId, resultHash, malleated)
      ).to.be.revertedWith("attestation failed");
    });
  });

  describe("registry metadata", function () {
    it("pins the ATTESTATION_SCHEME marker read by the TEE service", async function () {
      expect(await registry.ATTESTATION_SCHEME()).to.equal("eip712-v2");
    });
  });

  describe("ownership transfer (Ownable2Step)", function () {
    it("refuses to renounce ownership (a registry without an owner could never rotate keys)", async function () {
      await expect(registry.connect(owner).renounceOwnership())
        .to.be.revertedWith("renounceOwnership disabled");
      expect(await registry.owner()).to.equal(owner.address);
    });

    it("keeps the owner until the pending owner accepts", async function () {
      await expect(registry.connect(owner).transferOwnership(user.address))
        .to.emit(registry, "OwnershipTransferStarted")
        .withArgs(owner.address, user.address);

      expect(await registry.owner()).to.equal(owner.address);
      expect(await registry.pendingOwner()).to.equal(user.address);

      await expect(
        registry.connect(attacker).acceptOwnership()
      ).to.be.revertedWith("Ownable2Step: caller is not the new owner");

      await expect(registry.connect(user).acceptOwnership())
        .to.emit(registry, "OwnershipTransferred")
        .withArgs(owner.address, user.address);

      expect(await registry.owner()).to.equal(user.address);
      expect(await registry.pendingOwner()).to.equal(ethers.ZeroAddress);
    });
  });
});
