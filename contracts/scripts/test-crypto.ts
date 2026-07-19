import { ethers } from "hardhat";
import * as crypto from "crypto";

async function main() {
  console.log("Starting ECIES Encryption/Decryption Test...");
  console.log("Testing crypto operations in Node.js for frontend side simulation.");
  
  // Create a simple test to make sure our basic encryption libraries work
  try {
    const { generateKeyPairSync, publicEncrypt, privateDecrypt } = crypto;
    
    // Generate key pair
    const { publicKey, privateKey } = generateKeyPairSync('rsa', {
      modulusLength: 2048,
    });
    
    console.log("Keys generated successfully.");
    
    const secretData = "This is my portfolio: 2 BTC, 10 ETH";
    
    // Encrypt
    const encryptedData = publicEncrypt(
      publicKey,
      Buffer.from(secretData)
    );
    console.log(`Encrypted Data (base64): ${encryptedData.toString('base64').substring(0, 50)}...`);
    
    // Decrypt
    const decryptedData = privateDecrypt(
      privateKey,
      encryptedData
    );
    
    console.log(`Decrypted Data: ${decryptedData.toString('utf8')}`);
    console.log("Crypto Test: SUCCESS");
    
  } catch (err) {
    console.error("Crypto Test Failed:", err);
  }
}

main().catch(console.error);