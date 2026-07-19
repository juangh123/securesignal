// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface FtsoV2Interface {
    function getFeedById(bytes21 _feedId) external view returns (uint256 value, int8 decimals, uint64 timestamp);
}

contract FtsoV2Reader {
    FtsoV2Interface public ftsoV2;

    constructor(address _ftsoV2) {
        ftsoV2 = FtsoV2Interface(_ftsoV2);
    }

    function getPrice(bytes21 feedId) external view returns (uint256 value, int8 decimals, uint64 timestamp) {
        return ftsoV2.getFeedById(feedId);
    }
}