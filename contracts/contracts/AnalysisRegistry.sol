// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable2Step.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

/// @title AnalysisRegistry - SecureSignal Core Contract
/// @notice Register analysis tasks, record result commitments, and verify TEE attestations.
/// @dev Attestation convention (must match tee-service implementation):
///      The TEE holds a secp256k1 signing key whose address is registered in `teeAddress`.
///      For each completed task it signs EIP-712 typed data:
///          domain = { name: "SecureSignal", version: "2",
///                     chainId: block.chainid, verifyingContract: address(this) }
///          types  = { Attestation: [ {name: "taskId",     type: "uint256"},
///                                     {name: "resultHash", type: "bytes32"} ] }
///          digest = keccak256("\x19\x01" || domainSeparator || hashStruct(Attestation))
///      The 65-byte signature (r || s || v) is submitted on-chain as `attestation`.
///      `_verifyAttestation` recovers the signer with ecrecover and requires it to
///      equal the registered `teeAddress`.
///
///      Binding the domain to `chainId` + `verifyingContract` is what stops a
///      signature produced for one registry deployment (or one chain) from being
///      replayed against another one that registered the same TEE key. The older
///      EIP-191 revision ("\x19Ethereum Signed Message:\n64" over the raw 64-byte
///      payload) had no such binding; `ATTESTATION_SCHEME` below lets the TEE
///      service detect which convention the registry it talks to expects.
contract AnalysisRegistry is Ownable2Step, EIP712 {
    /// @notice Attestation convention implemented by this contract. The TEE
    ///         service reads it via eth_call and signs accordingly, so a service
    ///         build cannot silently pair with the wrong registry revision.
    string public constant ATTESTATION_SCHEME = "eip712-v2";

    bytes32 private constant ATTESTATION_TYPEHASH =
        keccak256("Attestation(uint256 taskId,bytes32 resultHash)");

    /// @dev `Status.Completed` is declared for ABI stability (the value 3 for
    ///      `Verified` and the frontend/tee status mapping depend on this
    ///      ordering) but is never assigned: `submitResult` moves a task straight
    ///      from Requested to Verified once the TEE attestation checks out.
    enum Status { None, Requested, Completed, Verified }

    struct Task {
        address user;
        bytes32 inputDataHash;   // Hash of encrypted input data (tamper-proof anchor)
        bytes32 resultHash;      // keccak256 of the result JSON produced by the TEE
        uint64  requestedAt;
        uint64  completedAt;
        Status  status;
    }

    // Public key of the currently verified TEE instance (used by users to encrypt data)
    bytes public activeTeePublicKey;
    // Verified TEE measurement (image hash), ensuring the designated open-source code is running
    bytes32 public expectedImageDigest;
    // Address of the TEE signing key; attestations must recover to this address
    address public teeAddress;

    /// @notice A key rotation that has been announced but is not active yet.
    /// @dev While pending, clients keep encrypting to the active key; the delay
    ///      gives users and monitoring a window to observe and react before the
    ///      new key takes effect. First-time registration (teeAddress == 0) is
    ///      exempt from the delay so a fresh deployment can start serving.
    struct PendingTeeKey {
        bytes newPublicKey;
        bytes32 newImageDigest;
        address newTeeAddress;
        uint64 executableAt;
        bool exists;
    }

    /// @notice Minimum delay between announcing and activating a rotation.
    uint64 public immutable teeRotationDelay;
    PendingTeeKey public pendingTeeKey;

    mapping(uint256 => Task) public tasks;
    uint256 public nextTaskId;

    event AnalysisRequested(uint256 indexed taskId, address indexed user, bytes32 inputDataHash);
    event ResultSubmitted(uint256 indexed taskId, bytes32 resultHash);
    event TeeKeyRotated(bytes newPublicKey, bytes32 imageDigest, address teeAddress);
    event TeeKeyRotationProposed(
        bytes newPublicKey,
        bytes32 imageDigest,
        address teeAddress,
        uint64 executableAt
    );
    event TeeKeyRotationCancelled(bytes newPublicKey, bytes32 imageDigest, address teeAddress);

    constructor(bytes32 _expectedImageDigest, uint256 _teeRotationDelay) EIP712("SecureSignal", "2") {
        require(_teeRotationDelay <= 365 days, "rotation delay too large");
        expectedImageDigest = _expectedImageDigest;
        teeRotationDelay = uint64(_teeRotationDelay);
    }

    /// @notice User registers an analysis task
    function requestAnalysis(bytes32 inputDataHash) external returns (uint256 taskId) {
        taskId = nextTaskId++;
        tasks[taskId] = Task({
            user: msg.sender,
            inputDataHash: inputDataHash,
            resultHash: bytes32(0),
            requestedAt: uint64(block.timestamp),
            completedAt: 0,
            status: Status.Requested
        });
        emit AnalysisRequested(taskId, msg.sender, inputDataHash);
    }

    /// @notice TEE (via a relayer) submits the result commitment + attestation signature
    /// @param taskId      The task previously opened with requestAnalysis
    /// @param resultHash  keccak256 of the result JSON
    /// @param attestation 65-byte EIP-712 signature of the TEE key over the
    ///                    Attestation(taskId, resultHash) struct (see contract docs)
    function submitResult(
        uint256 taskId,
        bytes32 resultHash,
        bytes calldata attestation
    ) external {
        Task storage t = tasks[taskId];
        require(t.status == Status.Requested, "invalid status");

        // Verify attestation: the TEE signing key must have signed (taskId, resultHash)
        require(_verifyAttestation(taskId, resultHash, attestation), "attestation failed");

        t.resultHash = resultHash;
        t.completedAt = uint64(block.timestamp);
        t.status = Status.Verified;
        emit ResultSubmitted(taskId, resultHash);
    }

    /// @dev Recover the attestation signer and compare against the registered TEE address.
    ///      Key rotation is a two-step, delayed flow (`proposeTeeKey` ->
    ///      `commitTeeKeyRotation`), so a swapped key is announced on-chain before
    ///      it can sign anything.
    ///      TODO(production): also verify a GCP Confidential Space JWT / Flare vTPM
    ///      attestation contract proof that `teeAddress` was generated inside the
    ///      expected image (`expectedImageDigest`). `expectedImageDigest` is a
    ///      registry record today, not an on-chain enforced measurement: the
    ///      signature check alone only proves possession of the registered key.
    function _verifyAttestation(
        uint256 taskId,
        bytes32 resultHash,
        bytes calldata attestation
    ) internal view returns (bool) {
        if (teeAddress == address(0)) return false;
        if (attestation.length != 65) return false;

        // EIP-712 digest: domain-separated by chainId + verifyingContract.
        bytes32 structHash = keccak256(
            abi.encode(ATTESTATION_TYPEHASH, taskId, resultHash)
        );
        bytes32 digest = _hashTypedDataV4(structHash);
        (address recovered, ECDSA.RecoverError err) = ECDSA.tryRecover(digest, attestation);
        return err == ECDSA.RecoverError.NoError && recovered == teeAddress;
    }

    /// @notice Announce a TEE key rotation (owner-only).
    /// @dev The new key only becomes active through `commitTeeKeyRotation` after
    ///      `teeRotationDelay` seconds, so clients see the change on-chain before
    ///      it can sign results. Re-proposing replaces the pending entry and
    ///      restarts the delay window. In a full deployment this step would also
    ///      verify an attestation proving the key was generated inside the
    ///      expected TEE image. The key format check is deliberately shallow
    ///      (length + 0x04 prefix): a full on-curve check is not practical
    ///      on-chain, but it rejects empty/garbage blobs that would otherwise
    ///      leave clients encrypting to an unusable key.
    function proposeTeeKey(
        bytes calldata newPublicKey,
        bytes32 newImageDigest,
        address newTeeAddress
    ) external onlyOwner {
        _validateTeeKey(newPublicKey, newImageDigest, newTeeAddress);
        uint64 executableAt = uint64(block.timestamp) + teeRotationDelay;
        pendingTeeKey = PendingTeeKey({
            newPublicKey: newPublicKey,
            newImageDigest: newImageDigest,
            newTeeAddress: newTeeAddress,
            executableAt: executableAt,
            exists: true
        });
        emit TeeKeyRotationProposed(
            newPublicKey,
            newImageDigest,
            newTeeAddress,
            executableAt
        );
    }

    /// @notice Activate a previously announced rotation once the delay elapsed.
    /// @dev First-time registration (no key registered yet) skips the delay so a
    ///      fresh deployment can start serving immediately.
    function commitTeeKeyRotation() external onlyOwner {
        PendingTeeKey memory pending = pendingTeeKey;
        require(pending.exists, "no pending rotation");
        require(
            teeAddress == address(0) || block.timestamp >= pending.executableAt,
            "rotation delay not elapsed"
        );

        activeTeePublicKey = pending.newPublicKey;
        expectedImageDigest = pending.newImageDigest;
        teeAddress = pending.newTeeAddress;
        delete pendingTeeKey;
        emit TeeKeyRotated(
            pending.newPublicKey,
            pending.newImageDigest,
            pending.newTeeAddress
        );
    }

    /// @notice Drop a pending rotation without activating it (owner-only).
    function cancelTeeKeyRotation() external onlyOwner {
        PendingTeeKey memory pending = pendingTeeKey;
        require(pending.exists, "no pending rotation");
        delete pendingTeeKey;
        emit TeeKeyRotationCancelled(
            pending.newPublicKey,
            pending.newImageDigest,
            pending.newTeeAddress
        );
    }

    /// @dev A registry without an owner can never rotate the TEE key again (and
    ///      a fresh deployment could never register the first one), so
    ///      renouncing ownership is disabled; hand over with Ownable2Step's
    ///      transferOwnership/acceptOwnership instead.
    function renounceOwnership() public view override {
        revert("renounceOwnership disabled");
    }

    function _validateTeeKey(
        bytes calldata newPublicKey,
        bytes32 newImageDigest,
        address newTeeAddress
    ) internal pure {
        require(newPublicKey.length == 65, "public key must be 65 bytes");
        require(newPublicKey[0] == 0x04, "public key must be uncompressed (0x04)");
        require(newImageDigest != bytes32(0), "zero image digest");
        require(newTeeAddress != address(0), "zero tee address");
    }
}
