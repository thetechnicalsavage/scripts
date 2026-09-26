#!/usr/bin/env python3
"""Generate an RSA keypair and the matching JWKS for a demo JWT issuer.

DEMO ONLY. In production your identity provider owns the signing key and
publishes the JWKS itself; you would never generate or hold it. This exists so
the ORDS MCP JWT profile can be exercised without standing up an IdP first.
"""
import json, base64
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

KID = "ords-mcp-demo-key"

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
open("mcp_signing_key.pem", "wb").write(
    key.private_bytes(serialization.Encoding.PEM,
                      serialization.PrivateFormat.PKCS8,
                      serialization.NoEncryption()))

n = key.public_key().public_numbers()
b64u = lambda i: base64.urlsafe_b64encode(
    i.to_bytes((i.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()

json.dump({"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID,
                     "n": b64u(n.n), "e": b64u(n.e)}]},
          open("jwks.json", "w"), indent=2)
print("wrote mcp_signing_key.pem (chmod 600 it) and jwks.json")
