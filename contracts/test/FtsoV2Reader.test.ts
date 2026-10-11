import { expect } from "chai";
import { ethers } from "hardhat";
import { FtsoV2Reader, MockFtsoV2 } from "../typechain-types";

describe("FtsoV2Reader", function () {
  // Feed id convention: 0x01 || "<SYMBOL>/USD" zero-padded to 21 bytes.
  const feedId = ethers.zeroPadBytes(
    ethers.concat([new Uint8Array([0x01]), ethers.toUtf8Bytes("BTC/USD")]),
    21
  );
  const feedValue = 65000_00000000n; // $65,000 at 8 decimals
  const feedDecimals = 8;
  const feedTimestamp = 1759000000n;

  async function deployFixture(): Promise<{ mock: MockFtsoV2; reader: FtsoV2Reader }> {
    const mockFactory = await ethers.getContractFactory("MockFtsoV2");
    const mock = await mockFactory.deploy();
    await mock.waitForDeployment();
    await mock.setFeed(feedId, feedValue, feedDecimals, feedTimestamp);

    const readerFactory = await ethers.getContractFactory("FtsoV2Reader");
    const reader = await readerFactory.deploy(await mock.getAddress());
    await reader.waitForDeployment();

    return { mock, reader };
  }

  it("stores the FtsoV2 address passed to the constructor", async function () {
    const { mock, reader } = await deployFixture();
    expect(await reader.ftsoV2()).to.equal(await mock.getAddress());
  });

  it("returns the (value, decimals, timestamp) tuple from getFeedById", async function () {
    const { reader } = await deployFixture();

    // getPrice is payable (non-view) since the Flare interface is, so the
    // return values are read through eth_call via staticCall.
    const [value, decimals, timestamp] = await reader.getPrice.staticCall(feedId);

    expect(value).to.equal(feedValue);
    expect(decimals).to.equal(feedDecimals);
    expect(timestamp).to.equal(feedTimestamp);
  });

  it("forwards the feedId unchanged to FtsoV2.getFeedById", async function () {
    const { mock, reader } = await deployFixture();

    await reader.getPrice(feedId);

    expect(await mock.lastFeedId()).to.equal(feedId);
    expect(await mock.callCount()).to.equal(1n);
  });

  it("passes msg.value through to FtsoV2.getFeedById", async function () {
    const { mock, reader } = await deployFixture();

    const [value] = await reader.getPrice.staticCall(feedId, { value: 12345n });
    expect(value).to.equal(feedValue);

    await reader.getPrice(feedId, { value: 12345n });
    expect(await mock.lastMsgValue()).to.equal(12345n);
  });
});
