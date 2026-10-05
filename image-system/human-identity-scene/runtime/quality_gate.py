#!/usr/bin/env python3
"""Fail-closed local identity/structure gate for Scene Identity v1."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis

ROOT = Path("/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System")
CONTRACT_PATH = ROOT / "image-agent/human-identity-scene/config/SCENE-CONTRACT.json"
MODEL_ROOT = ROOT / "image-agent/phase-7/candidate/assets/evaluator"
OUTPUT_ROOT = Path("/mnt/data/AI/Outputs/.aag-human-identity-scene-private")
FIXTURES = {
    "authorized-adult-01": Path("/mnt/data/AI/Outputs/2026-08-18T17-58-50-771Z-6967568841574511_00001_.png"),
    "authorized-baby-01": Path("/mnt/data/AI/Apps/AnythingLLM/storage/reference-images/20260819-004001-IMG_9101.jpg"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cosine(left, right) -> float:
    left = np.asarray(left, dtype=np.float32)
    right = np.asarray(right, dtype=np.float32)
    return float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right)))


def read_faces(app: FaceAnalysis, path: Path):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError("image cannot be decoded")
    faces = app.get(image)
    return image, faces


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-report", required=True, type=Path)
    parser.add_argument("--reference-validation", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    contract = json.loads(CONTRACT_PATH.read_text())
    contract_sha = sha256(CONTRACT_PATH)
    worker = json.loads(args.worker_report.read_text())
    reference = json.loads(args.reference_validation.read_text())
    artifact = Path(worker.get("artifact", {}).get("path", "")).resolve(strict=True)
    reference_path = Path(reference.get("reference_path", "")).resolve(strict=True)
    if OUTPUT_ROOT not in artifact.parents or artifact.is_symlink() or not artifact.is_file():
        raise ValueError("unsafe scene candidate path")
    if worker.get("scene_contract_sha256") != contract_sha or worker.get("contract_id") != contract["contract_id"]:
        raise ValueError("scene contract integrity mismatch")
    profile = contract["supported_profiles"].get(worker.get("scene_profile"))
    if not profile or worker.get("width") != profile["width"] or worker.get("height") != profile["height"]:
        raise ValueError("scene profile mismatch")
    if hashlib.sha256(str(worker.get("prompt", "")).encode()).hexdigest() != worker.get("prompt_sha256"):
        raise ValueError("scene prompt integrity mismatch")
    app = FaceAnalysis(name="buffalo_l", root=str(MODEL_ROOT), providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    reference_image, reference_faces = read_faces(app, reference_path)
    output_image, output_faces = read_faces(app, artifact)
    controls = {}
    for label, path in FIXTURES.items():
        _, faces = read_faces(app, path)
        if len(faces) != 1:
            raise ValueError("negative control face-count drift")
        controls[label] = np.asarray(faces[0].normed_embedding, dtype=np.float32)
    gate = contract["identity_gate"]
    record = {
        "schema_version": "aag.human-identity.scene-quality-gate.v1",
        "status": "FAIL",
        "contract_id": contract["contract_id"],
        "scene_contract_sha256": contract_sha,
        "prompt_sha256": worker["prompt_sha256"],
        "scene_profile": worker["scene_profile"],
        "candidate_sha256": sha256(artifact),
        "dimensions": [output_image.shape[1], output_image.shape[0]],
        "reference_face_count": len(reference_faces),
        "face_count": len(output_faces),
        "locked_floor": gate["cosine_floor"],
        "manual_visual_review_required": True,
        "automatic_scene_fidelity_scope": contract["scene_gate"]["automatic_scope"],
        "negative_identity_cosines": {},
    }
    expected_dimensions = [profile["width"], profile["height"]]
    if record["dimensions"] != expected_dimensions:
        record["error_code"] = "SCENE_IDENTITY_OUTPUT_DIMENSIONS_INVALID"
    elif len(reference_faces) != 1:
        record["error_code"] = "SCENE_IDENTITY_REFERENCE_DRIFT"
    elif len(output_faces) != 1:
        record["error_code"] = "SCENE_IDENTITY_OUTPUT_FACE_COUNT_INVALID"
    else:
        face = output_faces[0]
        reference_face = reference_faces[0]
        intended = cosine(face.normed_embedding, reference_face.normed_embedding)
        negatives = {label: cosine(face.normed_embedding, embedding) for label, embedding in controls.items() if sha256(reference_path) != sha256(FIXTURES[label])}
        margin = min((intended - value for value in negatives.values()), default=intended)
        x1, y1, x2, y2 = [float(value) for value in face.bbox]
        width, height = expected_dimensions
        occupancy = 100.0 * (x2 - x1) * (y2 - y1) / (width * height)
        ix1, iy1, ix2, iy2 = [max(0, int(value)) for value in face.bbox]
        crop = output_image[iy1:iy2, ix1:ix2]
        blur = float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()) if crop.size else 0.0
        evaluable = (
            gate["face_occupancy_percent"]["minimum"] <= occupancy <= gate["face_occupancy_percent"]["maximum"]
            and blur >= gate["face_blur_variance_minimum"]
            and float(face.det_score) >= gate["face_detection_confidence_minimum"]
            and x1 > 0 and y1 > 0 and x2 < width and y2 < height
        )
        record.update({
            "intended_cosine": intended,
            "negative_identity_cosines": negatives,
            "minimum_negative_margin": margin,
            "face_bbox": [x1, y1, x2, y2],
            "face_occupancy_percent": occupancy,
            "face_blur_variance": blur,
            "face_blur_variance_floor": gate["face_blur_variance_minimum"],
            "face_detection_confidence": float(face.det_score),
            "face_evaluable": evaluable,
            "structural_scene_result": "PASS" if evaluable else "FAIL",
        })
        if intended < gate["cosine_floor"] or margin <= 0:
            record["error_code"] = "SCENE_IDENTITY_QUALITY_REJECTED"
        elif not evaluable:
            record["error_code"] = "SCENE_IDENTITY_FACE_UNEVALUABLE"
        else:
            record["status"] = "PASS"
            record["automated_result"] = "PASS_IDENTITY_NUMERIC_AND_STRUCTURAL_PENDING_MANDATORY_VISUAL_VETO"
    if args.result.exists():
        raise FileExistsError(args.result)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.result.with_name("." + args.result.name + ".partial")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    temporary.chmod(0o440)
    temporary.replace(args.result)
    print(json.dumps({"status": record["status"], "error_code": record.get("error_code"), "intended_cosine": record.get("intended_cosine")}))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
