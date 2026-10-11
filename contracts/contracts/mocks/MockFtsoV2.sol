// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title MockFtsoV2 - test-only stand-in for Flare's FtsoV2
/// @notice Returns configured feed tuples and records the raw call arguments
///         (feedId + msg.value) so tests can assert pass-through behavior of
///         FtsoV2Reader without touching a live network.
contract MockFtsoV2 {
    struct Feed {
        uint256 value;
        int8 decimals;
        uint64 timestamp;
    }

    mapping(bytes21 => Feed) private _feeds;

    uint256 public lastMsgValue;
    bytes21 public lastFeedId;
    uint256 public callCount;

    function setFeed(
        bytes21 feedId,
        uint256 value,
        int8 decimals,
        uint64 timestamp
    ) external {
        _feeds[feedId] = Feed(value, decimals, timestamp);
    }

    function getFeedById(
        bytes21 feedId
    ) external payable returns (uint256 value, int8 decimals, uint64 timestamp) {
        Feed storage feed = _feeds[feedId];
        lastMsgValue = msg.value;
        lastFeedId = feedId;
        callCount += 1;
        return (feed.value, feed.decimals, feed.timestamp);
    }
}
