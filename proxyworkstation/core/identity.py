"""Media identity.

Identity is the content hash (SHA-256 over the whole file), never the file
name. Two clips with identical names but different bytes get different
identities, so a same-named replacement can never be silently linked.
"""

import hashlib
import os

from .errors import MediaReadError

_HASH_CHUNK = 1024 * 1024


def sha256_file(path, chunk=_HASH_CHUNK):
    if not os.path.isfile(path):
        raise MediaReadError(f"file not found: {path}")
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def identity_for(path):
    """Return the stable identity dict used as the primary media key."""
    return {"content_hash": sha256_file(path), "size_bytes": os.path.getsize(path)}


def verify_hash(path, expected_hash):
    """Re-hash a file and compare; used after relocation or suspected tampering."""
    actual = sha256_file(path)
    return actual == expected_hash, actual
