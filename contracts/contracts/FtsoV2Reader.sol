// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface FtsoV2Interface {
    // Flare's live FtsoV2.getFeedById is payable: it forwards to a fee-charging
    // provider, so the caller value must be propagated, not dropped.
    function getFeedById(bytes21 _feedId) external payable returns (uint256 value, int8 decimals, uint64 timestamp);
}

contract FtsoV2Reader {
    FtsoV2Interface public ftsoV2;

    constructor(address _ftsoV2) {
        ftsoV2 = FtsoV2Interface(_ftsoV2);
    }

    // Payable for the same reason as the interface above; a plain eth_call
    // without value still works because no value is required for reads.
    function getPrice(bytes21 feedId) external payable returns (uint256 value, int8 decimals, uint64 timestamp) {
        return ftsoV2.getFeedById{value: msg.value}(feedId);
    }
}
