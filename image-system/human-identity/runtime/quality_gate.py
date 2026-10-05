#!/usr/bin/env python3
"""Phase 7R3 frozen identity gate plus prospective composition contract."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis

from composition_contract import (
    MATRIX_PATH,
    QUALITY_PATH,
    classify_prompt,
    composition_failures,
    geometry_metrics,
    quality_contract,
)
from evaluator_core import detect_faces

ROOT = Path("/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System")
P7 = ROOT / "image-agent/phase-7"
P7R3 = ROOT / "image-agent/phase-7r3"
OUTPUT_ROOT = Path("/mnt/data/AI/Outputs/.aag-human-identity-private")
MODEL_ROOT = P7 / "candidate/assets/evaluator"
CALIBRATION = ROOT / "image-agent/phase-4/identity/metrics/INSTANTID-EMBEDDING-CALIBRATION.json"
CALIBRATION_SHA256 = "294abdf4c67a13d8ac7680c442574cbc70eb9e78a765e961dce6b52a92298544"
IDENTITY_FLOOR = 0.5026416499167681
FIXTURES = {
    "authorized-adult-01": (
        Path("/mnt/data/AI/Outputs/2026-08-18T17-58-50-771Z-6967568841574511_00001_.png"),
        "8b131e3030a094173004ae17df02b9fa94d523cb273398b027ea6bb31e1f2c61",
    ),
    "authorized-baby-01": (
        Path("/mnt/data/AI/Apps/AnythingLLM/storage/reference-images/20260819-004001-IMG_9101.jpg"),
        "93665635711952c6a5da892bea90cc892b7c0a4a6748416e13a69ffd124eced6",
    ),
}
IDENTITY_TO_FIXTURE = {"adult": "authorized-adult-01", "baby": "authorized-baby-01"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_faces(app: FaceAnalysis, path: Path):
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("artifact decode failed")
    detected = detect_faces(app, image)
    return detected.faces, (image.shape[1], image.shape[0]), image, detected


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right)))


def landmark_geometry_valid(face) -> bool:
    keypoints = np.asarray(getattr(face, "kps", []), dtype=np.float32)
    if keypoints.shape != (5, 2) or not np.isfinite(keypoints).all():
        return False
    eye_distance = max(1.0, float(np.linalg.norm(keypoints[0] - keypoints[1])))
    return bool(
        abs(float(keypoints[0, 1] - keypoints[1, 1])) < 0.35 * eye_distance
        and float(keypoints[2, 1]) > float((keypoints[0, 1] + keypoints[1, 1]) / 2)
        and float((keypoints[3, 1] + keypoints[4, 1]) / 2) > float(keypoints[2, 1])
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-report", required=True)
    parser.add_argument("--reference-validation", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    worker = json.loads(Path(args.worker_report).read_text())
    reference = json.loads(Path(args.reference_validation).read_text())
    target = Path(args.result)
    if worker.get("status") != "PASS" or reference.get("status") != "PASS":
        raise ValueError("quality gate inputs are not PASS")
    if sha256(CALIBRATION) != CALIBRATION_SHA256:
        raise ValueError("frozen evaluator calibration hash drift")
    calibration = json.loads(CALIBRATION.read_text())
    if abs(float(calibration["candidate_screening"]["locked_floor"]) - IDENTITY_FLOOR) > 1e-15:
        raise ValueError("frozen identity floor drift")

    artifact = Path(worker["artifact"]["path"]).resolve(strict=True)
    if (
        OUTPUT_ROOT.resolve(strict=True) not in artifact.parents
        or artifact.is_symlink()
        or not artifact.is_file()
        or sha256(artifact) != worker["artifact"]["sha256"]
    ):
        raise ValueError("unsafe or changed artifact")

    reference_kind = reference.get("reference_kind")
    intended_fixture = reference.get("fixture_id")
    if reference_kind == "historical_validation_fixture":
        if intended_fixture not in FIXTURES or reference.get("original_sha256") != FIXTURES[intended_fixture][1]:
            raise ValueError("historical validation fixture provenance is inconsistent")
    elif reference_kind == "trusted_runtime_reference":
        if intended_fixture is not None:
            raise ValueError("trusted runtime reference must not impersonate a fixture")
    else:
        raise ValueError("reference kind is outside the Production-v1 eligibility contract")
    prompt_family, prompt_identity = classify_prompt(str(worker.get("prompt", "")))
    if reference_kind == "historical_validation_fixture" and IDENTITY_TO_FIXTURE[prompt_identity] != intended_fixture:
        raise ValueError("prompt identity and authorized reference fixture do not match")

    reference_path = Path(reference["reference_path"]).resolve(strict=True)
    reference_root = Path("/mnt/data/AI/Apps/AnythingLLM/storage/aag-human-identity-state/references").resolve(strict=True)
    if reference_root not in reference_path.parents or reference_path.is_symlink() or not reference_path.is_file() or sha256(reference_path) != reference["reference_sha256"]:
        raise ValueError("unsafe or changed request-bound reference")

    app = FaceAnalysis(name="buffalo_l", root=str(MODEL_ROOT), providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    controls = {}
    for key, (path, _) in FIXTURES.items():
        faces, _, _, _ = read_faces(app, path)
        if len(faces) != 1:
            raise ValueError("control face-count drift")
        controls[key] = np.asarray(faces[0].normed_embedding, dtype=np.float32)

    intended_faces, _, _, _ = read_faces(app, reference_path)
    if len(intended_faces) != 1:
        raise ValueError("request-bound reference face-count drift")
    intended_control = np.asarray(intended_faces[0].normed_embedding, dtype=np.float32)

    found, dimensions, image, detection = read_faces(app, artifact)
    record = {
        "schema_version": "aag.human-identity.quality-gate.v1",
        "status": "FAIL",
        "candidate_sha256": sha256(artifact),
        "dimensions": list(dimensions),
        "face_count": len(found),
        "locked_floor": IDENTITY_FLOOR,
        "calibration_sha256": CALIBRATION_SHA256,
        "composition_contract_sha256": sha256(QUALITY_PATH),
        "preregistered_matrix_sha256": sha256(MATRIX_PATH),
        "evaluator_algorithm_source": "Phase 4 evaluator-v1 with unchanged Phase 7R confirmed padded-scale detector recovery",
        "detector_model_changed": False,
        "recognition_model_changed": False,
        "identity_floor_changed": False,
        "detection_path": detection.path,
        "detection_confirmations": detection.confirmations,
        "reference_kind": reference_kind,
        "intended_fixture_id": intended_fixture,
        "reference_sha256": reference["reference_sha256"],
        "quality_validation_scope": "historical-regression-fixture" if reference_kind == "historical_validation_fixture" else "dynamic-reference-specific",
        "prompt_contract_id": prompt_family,
        "prompt_contract_identity": prompt_identity,
        "manual_visual_review_required": True,
        "raw_embedding_written": False,
        "negative_identity_cosines": {},
    }

    if dimensions != (896, 1152):
        record["error_code"] = "OUTPUT_DIMENSIONS_INVALID"
    elif image.dtype != np.uint8 or not np.isfinite(image).all() or float(image.std()) < 8:
        record["error_code"] = "OUTPUT_CORRUPT_OR_COLLAPSED"
    elif len(found) == 0:
        record["error_code"] = "OUTPUT_NO_FACE"
    elif len(found) > 1:
        record["error_code"] = "OUTPUT_MULTIPLE_FACES"
    else:
        face = found[0]
        embedding = np.asarray(face.normed_embedding, dtype=np.float32)
        bbox = [float(value) for value in face.bbox]
        geometry = geometry_metrics(bbox, dimensions)
        record.update(
            face_bbox=bbox,
            face_detection_confidence=float(face.det_score),
            embedding_finite=bool(np.isfinite(embedding).all()),
            embedding_norm=float(np.linalg.norm(embedding)),
            **geometry,
        )
        if embedding.shape != (512,) or not np.isfinite(embedding).all():
            record["error_code"] = "OUTPUT_IDENTITY_UNEVALUABLE"
        else:
            intended = cosine(embedding, intended_control)
            negatives = {
                key: cosine(embedding, value)
                for key, value in controls.items()
                if reference_kind != "historical_validation_fixture" or key != intended_fixture
            }
            margin = min(intended - value for value in negatives.values())
            record.update(
                intended_cosine=intended,
                negative_identity_cosines=negatives,
                minimum_negative_margin=margin,
            )
            if intended < IDENTITY_FLOOR or margin <= 0:
                record["error_code"] = "IDENTITY_QUALITY_REJECTED"
            else:
                x1, y1, x2, y2 = [max(0, int(value)) for value in bbox]
                crop = image[y1:y2, x1:x2]
                blur = (
                    float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
                    if crop.size
                    else 0.0
                )
                landmark_ok = landmark_geometry_valid(face)
                composition_errors = composition_failures(prompt_family, geometry)
                strict_inside = bbox[0] > 0 and bbox[1] > 0 and bbox[2] < dimensions[0] and bbox[3] < dimensions[1]
                universal = quality_contract()["universal"]
                artifact_errors = []
                if blur < universal["face_blur_variance_minimum"]:
                    artifact_errors.append("FACE_BLUR_BELOW_FLOOR")
                if not landmark_ok:
                    artifact_errors.append("LANDMARK_GEOMETRY_INVALID")
                if float(face.det_score) < universal["face_detection_confidence_minimum"]:
                    artifact_errors.append("FACE_DETECTION_CONFIDENCE_BELOW_FLOOR")
                if not strict_inside:
                    artifact_errors.append("FACE_BBOX_TOUCHES_IMAGE_EDGE")
                record.update(
                    prompt_composition_result="PASS" if not composition_errors else "FAIL",
                    composition_failures=composition_errors,
                    prompt_adherence_proxy="PASS" if not composition_errors else "FAIL",
                    severe_artifact_result="PASS_CONSERVATIVE_LOCAL_HEURISTICS" if not artifact_errors else "FAIL",
                    artifact_failures=artifact_errors,
                    face_blur_variance=blur,
                    face_blur_variance_floor=universal["face_blur_variance_minimum"],
                    landmark_geometry_valid=landmark_ok,
                    bbox_strictly_inside_image=strict_inside,
                )
                if composition_errors:
                    record["error_code"] = "OUTPUT_PROMPT_COMPOSITION_REJECTED"
                elif artifact_errors:
                    record["error_code"] = "OUTPUT_SEVERE_ARTIFACT_SUSPECTED"
                else:
                    record.update(
                        status="PASS",
                        automated_result="PASS_IDENTITY_NUMERIC_STRUCTURAL_COMPOSITION_ARTIFACT_SCREEN",
                    )

    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".partial")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    temporary.chmod(0o440)
    temporary.replace(target)
    print(json.dumps({
        "status": record["status"],
        "error_code": record.get("error_code"),
        "intended_cosine": record.get("intended_cosine"),
        "prompt_contract_id": record.get("prompt_contract_id"),
    }))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
