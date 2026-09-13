"""Face detection via mediapipe.tasks.python.vision (API nova, compatível Python 3.13)."""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
MODEL_CACHE = Path.home() / ".cache" / "mediapipe" / "blaze_face_short_range.tflite"


def _ensure_model() -> Path:
    if MODEL_CACHE.exists():
        return MODEL_CACHE
    MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    print(f"Baixando modelo mediapipe (~230KB) pra {MODEL_CACHE}...", file=sys.stderr)
    urllib.request.urlretrieve(MODEL_URL, MODEL_CACHE)
    return MODEL_CACHE


def detect_faces(video_path: Path, sample_rate_hz: float = 2.0) -> list[dict]:
    """Sample frames a `sample_rate_hz` e detecta rostos. Retorna [{t, face: {x,y,w,h} | None}]."""
    model_path = _ensure_model()
    options = mp_vision.FaceDetectorOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        min_detection_confidence=0.5,
    )
    detector = mp_vision.FaceDetector.create_from_options(options)

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = total_frames / fps
    step_s = 1.0 / sample_rate_hz

    zones = []
    t = 0.0
    while t < duration_s:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = detector.detect(mp_image)
        face = None
        if result.detections:
            d = max(result.detections, key=lambda x: x.bounding_box.width * x.bounding_box.height)
            bbox = d.bounding_box
            face = {
                "x": round(bbox.origin_x / w, 4),
                "y": round(bbox.origin_y / h, 4),
                "w": round(bbox.width / w, 4),
                "h": round(bbox.height / h, 4),
            }
        zones.append({"t": round(t, 3), "face": face})
        t += step_s

    cap.release()
    return zones


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--sample-rate", type=float, default=2.0, help="Hz")
    args = p.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erro: input não existe: {input_path}", file=sys.stderr)
        return 1

    zones = detect_faces(input_path, sample_rate_hz=args.sample_rate)
    Path(args.output).write_text(json.dumps(zones, indent=2))
    print(f"OK: {len(zones)} samples processadas, {sum(1 for z in zones if z['face'])} com rosto")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
