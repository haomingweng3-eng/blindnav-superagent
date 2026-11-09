import hashlib
import pytest

from mobilio_sanpo_ab import (validate_trace_inventory, validate_trace_frame_coverage,
                              validate_frozen_frame_keys, validate_trace_code_state,
                              validate_loaded_model_revisions, validate_model_cache_files,
                              verified_model_snapshot)


def test_inventory_rejects_changed_inputs_and_path_escape(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "frame.png").write_bytes(b"original")
    weights = tmp_path / "v5.pt"
    weights.write_bytes(b"weights")
    sha = lambda b: hashlib.sha256(b).hexdigest()
    manifest = {"schema": "e3-input-inventory/1", "missing": [], "files": [
        {"path": "frame.png", "bytes": 8, "sha256": sha(b"original")}],
        "v5_sha256": sha(b"weights")}
    assert validate_trace_inventory(manifest, root, weights) == 1
    (root / "frame.png").write_bytes(b"modified")
    with pytest.raises(ValueError, match="mismatch"):
        validate_trace_inventory(manifest, root, weights)
    manifest["files"][0]["path"] = "../v5.pt"
    with pytest.raises(ValueError, match="outside"):
        validate_trace_inventory(manifest, root, weights)


def test_inventory_requires_exact_runtime_frame_and_file_set():
    manifest = {"frame_keys": 1, "files": [{"path": p} for p in [
        "s/description.json", "s/camera_chest/fixed_camera_poses.csv",
        "s/camera_chest/left/video_frames/000003.png",
        "s/camera_chest/left/segmentation_masks/000003.png",
        "s/camera_chest/left/depth_maps/000003.float16.gz"]]}
    assert validate_trace_frame_coverage(manifest, {"s": [3]}, "camera_chest") == 1
    with pytest.raises(ValueError, match="frame count"):
        validate_trace_frame_coverage(manifest, {"s": [3, 4]}, "camera_chest")
    manifest["files"].pop()
    with pytest.raises(ValueError, match="file set"):
        validate_trace_frame_coverage(manifest, {"s": [3]}, "camera_chest")


def test_equal_count_but_different_frames_are_rejected(tmp_path):
    baseline = tmp_path / "baseline.csv"
    baseline.write_text("condition,arm,session,frame\nproduction,mobilio,s,3\n")
    digest = hashlib.sha256(baseline.read_bytes()).hexdigest()
    assert validate_frozen_frame_keys(baseline, {"s": [3]}, digest) == 1
    with pytest.raises(ValueError, match="frame keys"):
        validate_frozen_frame_keys(baseline, {"s": [4]}, digest)
    with pytest.raises(ValueError, match="hash"):
        validate_frozen_frame_keys(baseline, {"s": [3]}, "0" * 64)


def test_equal_frame_set_in_different_order_is_rejected(tmp_path):
    baseline = tmp_path / "baseline.csv"
    baseline.write_text("condition,arm,session,frame\nproduction,mobilio,s,3\nproduction,mobilio,s,4\n")
    digest = hashlib.sha256(baseline.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="order"):
        validate_frozen_frame_keys(baseline, {"s": [4, 3]}, digest)


def test_trace_code_state_rejects_wrong_commit_and_uncommitted_changes():
    assert validate_trace_code_state("a" * 40, "", "a" * 40) == "a" * 40
    with pytest.raises(ValueError, match="commit"):
        validate_trace_code_state("a" * 40, "", "b" * 40)
    with pytest.raises(ValueError, match="uncommitted"):
        validate_trace_code_state("a" * 40, " M pc/blindnav_pc/mobilio_surface.py", "a" * 40)


def test_loaded_model_revision_must_match_inventory():
    cache = {"seg": {"revision": "abc"}, "depth": {"revision": "def"}}
    assert validate_loaded_model_revisions(cache, {"seg": "abc", "depth": "def"})
    with pytest.raises(ValueError, match="revision"):
        validate_loaded_model_revisions(cache, {"seg": "abc", "depth": None})


def test_model_cache_file_tampering_is_rejected(tmp_path):
    file = tmp_path / "processor.json"
    file.write_bytes(b"config")
    inventory = {"depth": {"revision": "abc", "cache_files": [{
        "name": "processor.json", "bytes": 6,
        "sha256": hashlib.sha256(b"config").hexdigest()}]}}
    resolver = lambda repo, name, revision: str(file)
    assert validate_model_cache_files(inventory, resolver) == 1
    file.write_bytes(b"change")
    with pytest.raises(ValueError, match="model cache mismatch"):
        validate_model_cache_files(inventory, resolver)


def test_snapshot_rejects_unlisted_alternative_weights(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    inventory = {"depth": {"revision": "abc", "cache_files": [{"name": "config.json"}]}}
    resolver = lambda repo, name, revision: str(tmp_path / name)
    assert verified_model_snapshot(inventory, "depth", resolver) == str(tmp_path)
    (tmp_path / "model.safetensors").write_bytes(b"other")
    with pytest.raises(ValueError, match="file set"):
        verified_model_snapshot(inventory, "depth", resolver)


def test_snapshot_rejects_unlisted_subdirectory(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "extra").mkdir()
    inventory = {"depth": {"revision": "abc", "cache_files": [{"name": "config.json"}]}}
    resolver = lambda repo, name, revision: str(tmp_path / name)
    with pytest.raises(ValueError, match="file set"):
        verified_model_snapshot(inventory, "depth", resolver)
