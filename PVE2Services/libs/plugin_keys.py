#!/usr/bin/env python
"""Generate the ed25519 keypair used to sign plugin packages.

Usage:
    pve2-generate-keys <public-key-path> <private-key-path>
    pve2-generate-keys            # keypair standalone printed to stdout

- The private key NEVER goes into the repo: put it in GitHub Secrets
  (``PVE2_PLUGIN_SIGNING_KEY``) or a local secure location for publishing.
- The public key is committed to the repo (`keys/plugin_signing.pub`); the
  core loader verifies package signatures against it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PVE2Services.libs.plugin_signing import SigningKeyPair


def main() -> int:
    args = sys.argv[1:]
    keypair = SigningKeyPair.generate()
    if not args:
        print("--- PUBLIC KEY (commit at keys/plugin_signing.pub) ---")
        print(keypair.public_pem().decode("ascii"), end="")
        print("--- PRIVATE KEY (GitHub Secret PVE2_PLUGIN_SIGNING_KEY; "
              "NEVER commit) ---")
        print(keypair.private_pem().decode("ascii"), end="")
        return 0
    if len(args) > 2:
        raise SystemExit("usage: generate_keys.py [<public-key-path> <private-key-path>]")
    pub_path = Path(args[0])
    priv_path = Path(args[1]) if len(args) > 1 else Path("plugin_signing.pem")
    keypair.write_public_pem(pub_path)
    keypair.write_private_pem(priv_path)
    print(f"Public key written to   {pub_path}  (commit this)")
    print(f"Private key written to  {priv_path}  (NEVER commit; used by release CI)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
