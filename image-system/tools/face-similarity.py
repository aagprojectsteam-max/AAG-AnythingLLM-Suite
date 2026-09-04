#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis


def largest_embedding(app, filename):
    image = cv2.imread(str(filename))
    if image is None:
        raise ValueError("image cannot be decoded")
    faces = app.get(image)
    if not faces:
        raise ValueError("no face detected")
    face = max(faces, key=lambda item: float((item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1])))
    embedding = np.asarray(face.normed_embedding, dtype=np.float32)
    return embedding, [float(value) for value in face.bbox.tolist()], len(faces)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--models-root", type=Path, default=Path("/mnt/data/AI/Apps/AnythingLLM/AAG-Reference-Identity/models"))
    args = parser.parse_args()
    for filename in (args.source, args.candidate):
        if not filename.resolve().is_file():
            raise SystemExit("input image is unavailable")
    app = FaceAnalysis(name="buffalo_l", root=str(args.models_root), providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    source, source_box, source_faces = largest_embedding(app, args.source.resolve())
    candidate, candidate_box, candidate_faces = largest_embedding(app, args.candidate.resolve())
    cosine = float(np.dot(source, candidate) / (np.linalg.norm(source) * np.linalg.norm(candidate)))
    print(json.dumps({
        "engine": "InsightFace/buffalo_l", "provider": "CPUExecutionProvider",
        "source": str(args.source.resolve()), "candidate": str(args.candidate.resolve()),
        "cosine_similarity": cosine, "source_faces": source_faces, "candidate_faces": candidate_faces,
        "source_bbox": source_box, "candidate_bbox": candidate_box,
        "note": "Numeric evidence supplements but does not replace visual acceptance."
    }, indent=2))


if __name__ == "__main__":
    main()
