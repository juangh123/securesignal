use std::env;
use std::io::{self, Write};
use std::process;

use aws_nitro_enclaves_nsm_api::api::{Request, Response};
use aws_nitro_enclaves_nsm_api::driver::{nsm_exit, nsm_init, nsm_process_request};
use serde_bytes::ByteBuf;

fn parse_hex_arg(name: &str) -> Option<Vec<u8>> {
    let value = match env::var(name) {
        Ok(value) if !value.is_empty() => value,
        _ => return None,
    };
    let value = value.strip_prefix("0x").unwrap_or(&value);
    if value.len() % 2 != 0 {
        eprintln!("{name} must contain an even number of hex characters");
        process::exit(2);
    }
    let mut bytes = Vec::with_capacity(value.len() / 2);
    for index in (0..value.len()).step_by(2) {
        match u8::from_str_radix(&value[index..index + 2], 16) {
            Ok(byte) => bytes.push(byte),
            Err(_) => {
                eprintln!("{name} contains invalid hex");
                process::exit(2);
            }
        }
    }
    Some(bytes)
}

fn main() {
    let nonce = parse_hex_arg("NSM_NONCE_HEX");
    let user_data = parse_hex_arg("NSM_USER_DATA_HEX");
    let public_key = parse_hex_arg("NSM_PUBLIC_KEY_HEX");

    let descriptor = nsm_init();
    if descriptor < 0 {
        eprintln!("nsm_init failed: {descriptor}");
        process::exit(1);
    }

    let request = Request::Attestation {
        user_data: user_data.map(ByteBuf::from),
        nonce: nonce.map(ByteBuf::from),
        public_key: public_key.map(ByteBuf::from),
    };

    let result = match nsm_process_request(descriptor, request) {
        Response::Attestation { document } => document,
        Response::Error(error) => {
            nsm_exit(descriptor);
            eprintln!("NSM attestation failed: {error:?}");
            process::exit(1);
        }
        other => {
            nsm_exit(descriptor);
            eprintln!("unexpected NSM response: {other:?}");
            process::exit(1);
        }
    };
    nsm_exit(descriptor);

    if let Err(error) = io::stdout().write_all(&result) {
        eprintln!("failed to write attestation document: {error}");
        process::exit(1);
    }
}
