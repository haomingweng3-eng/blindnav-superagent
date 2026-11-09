"""Download the exact public DAV2 snapshot recorded in the E3 inventory.

Only the three recorded model files are retrieved; each byte count and SHA256
must match. Existing mismatched files are refused rather than overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    model_id = "depth-anything/Depth-Anything-V2-Small-hf"
    spec = inventory["model_cache_inventory"][model_id]
    args.out.mkdir(parents=True, exist_ok=True)
    records = []
    for item in spec["cache_files"]:
        name = item["name"]
        if name not in {"config.json", "model.safetensors", "preprocessor_config.json"}:
            raise ValueError(f"Unexpected frozen model filename: {name}")
        path = args.out / name
        url = f"https://huggingface.co/{model_id}/resolve/{spec['revision']}/{name}"
        reused = path.exists()
        if not reused:
            temp = path.with_suffix(path.suffix + ".part")
            with urllib.request.urlopen(url, timeout=90) as response, temp.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            digest = hashlib.sha256(temp.read_bytes()).hexdigest()
            if temp.stat().st_size != item["bytes"] or digest != item["sha256"]:
                raise ValueError(f"Frozen model mismatch: {name}")
            temp.replace(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.stat().st_size != item["bytes"] or digest != item["sha256"]:
            raise ValueError(f"Existing model mismatch, refusing overwrite: {path}")
        records.append({"name": name, "url": url, "bytes": path.stat().st_size,
                        "sha256": digest, "reused": reused})
        print(json.dumps(records[-1]), flush=True)
    result = {"model_id": model_id, "revision": spec["revision"], "files": records,
              "inventory_sha256": hashlib.sha256(args.inventory.read_bytes()).hexdigest()}
    (args.out / "download_manifest.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
