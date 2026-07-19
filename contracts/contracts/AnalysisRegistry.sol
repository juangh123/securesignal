// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";

/// @title AnalysisRegistry - SecureSignal Core Contract
/// @notice Register analysis tasks, record result commitments, and verify TEE attestations.
/// @dev Attestation convention (must match tee-service implementation):
///      The TEE holds a secp256k1 signing key whose address is registered in `teeAddress`.
///      For each completed task it produces an Ethereum signed message (EIP-191
///      personal_sign) over the raw 64-byte `abi.encodePacked(taskId, resultHash)`
///      payload, i.e. the digest
///          keccak256("\x19Ethereum Signed Message:\n64" || taskId || resultHash)
///      The 65-byte signature (r || s || v) is submitted on-chain as `attestation`.
///      `_verifyAttestation` recovers the signer with ecrecover and requires it to
///      equal the registered `teeAddress`.
contract AnalysisRegistry is Ownable {
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

    mapping(uint256 => Task) public tasks;
    uint256 public nextTaskId;

    event AnalysisRequested(uint256 indexed taskId, address indexed user, bytes32 inputDataHash);
    event ResultSubmitted(uint256 indexed taskId, bytes32 resultHash);
    event TeeKeyRotated(bytes newPublicKey, bytes32 imageDigest, address teeAddress);

    constructor(bytes32 _expectedImageDigest) {
        expectedImageDigest = _expectedImageDigest;
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
    /// @param attestation 65-byte EIP-191 signature of the TEE key over
    ///                    abi.encodePacked(taskId, resultHash)
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
    ///      TODO(production): also verify a GCP Confidential Space JWT / Flare vTPM
    ///      attestation contract proof that `teeAddress` was generated inside the
    ///      expected image (`expectedImageDigest`); the signature check alone only
    ///      proves possession of the registered key.
    function _verifyAttestation(
        uint256 taskId,
        bytes32 resultHash,
        bytes calldata attestation
    ) internal view returns (bool) {
        if (teeAddress == address(0)) return false;
        if (attestation.length != 65) return false;

        // EIP-191 personal_sign digest of the 64-byte packed (taskId, resultHash)
        bytes32 digest = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n64", taskId, resultHash)
        );
        (address recovered, ECDSA.RecoverError err) = ECDSA.tryRecover(digest, attestation);
        return err == ECDSA.RecoverError.NoError && recovered == teeAddress;
    }

    /// @notice Owner rotates the TEE key after a fresh attestation of the new instance.
    /// @dev Registers both the new encryption public key and the new signing address.
    ///      In a full deployment this would require a valid attestation proving the
    ///      key was generated inside the expected TEE image.
    function rotateTeeKey(
        bytes calldata newPublicKey,
        bytes32 newImageDigest,
        address newTeeAddress
    ) external onlyOwner {
        require(newPublicKey.length > 0, "empty public key");
        require(newTeeAddress != address(0), "zero tee address");
        activeTeePublicKey = newPublicKey;
        expectedImageDigest = newImageDigest;
        teeAddress = newTeeAddress;
        emit TeeKeyRotated(newPublicKey, newImageDigest, newTeeAddress);
    }
}
