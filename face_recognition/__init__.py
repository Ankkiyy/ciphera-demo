# detector.py

from collections import Counter
# detector.py

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Literal, Optional, Sequence, Tuple
import io
import pickle
import re
import shutil
import sys
import time
import uuid

import face_recognition
from PIL import Image, ImageDraw

try:
    import dlib  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    dlib = None

DEFAULT_ENCODINGS_PATH = Path("face_recognition/output/encodings.pkl")
TRAINING_DIR = Path("face_recognition/training")
VALIDATION_DIR = Path("face_recognition/validation")
OUTPUT_DIR = Path("face_recognition/output")
BOUNDING_BOX_COLOR = "blue"
TEXT_COLOR = "white"

TRAINING_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
VALIDATION_DIR.mkdir(exist_ok=True)


def _iter_image_files(directory: Path) -> Iterable[Path]:
    for extension in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        yield from directory.rglob(extension)


def slugify_name(first_name: str, last_name: str, email: Optional[str] = None) -> str:
    base_parts = [first_name or "", last_name or ""]
    base = "-".join(filter(None, base_parts)).lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")

    suffix = ""
    if email:
        local_part = email.split("@", maxsplit=1)[0].lower()
        suffix = re.sub(r"[^a-z0-9]+", "", local_part)

    if suffix:
        slug = f"{base}-{suffix}" if base else suffix
    else:
        slug = base

    return slug or f"user-{uuid.uuid4().hex[:8]}"


def _prepare_training_dir(person_slug: str, replace: bool = True) -> Path:
    target = TRAINING_DIR / person_slug
    if replace and target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def store_face_samples(
    person_slug: str,
    samples: Sequence[bytes],
    replace: bool = True,
) -> List[Path]:
    target_dir = _prepare_training_dir(person_slug, replace=replace)
    saved_paths: List[Path] = []
    timestamp = int(time.time() * 1000)

    for index, sample in enumerate(samples, start=1):
        filename = f"{timestamp}_{index:02d}.jpg"
        destination = target_dir / filename
        destination.write_bytes(sample)
        saved_paths.append(destination)

    return saved_paths


@lru_cache(maxsize=1)
def _load_cached_encodings(encodings_path: str):
    path = Path(encodings_path)
    with path.open("rb") as handle:
        return pickle.load(handle)


def load_encodings(encodings_location: Path = DEFAULT_ENCODINGS_PATH):
    location = Path(encodings_location)
    if not location.exists():
        raise FileNotFoundError(
            f"Encodings file not found at {location}. Run encode_known_faces first."
        )
    return _load_cached_encodings(str(location.resolve()))


def clear_encodings_cache() -> None:
    _load_cached_encodings.cache_clear()


def encode_known_faces(
    mode: Literal["cpu", "gpu"] = "cpu",
    detector_model: Optional[str] = None,
    encodings_location: Path = DEFAULT_ENCODINGS_PATH,
    num_jitters: Optional[int] = None,
    encoding_model: Optional[str] = None,
    verbose: bool = True,
) -> None:
    if mode not in {"cpu", "gpu"}:
        raise ValueError("mode must be either 'cpu' or 'gpu'")

    use_cuda = (
        mode == "gpu"
        and dlib is not None
        and bool(getattr(dlib, "DLIB_USE_CUDA", False))
    )

    effective_mode = "gpu" if use_cuda else "cpu"
    if mode == "gpu" and not use_cuda and verbose:
        print("CUDA support not detected; falling back to CPU pipeline.")

    if effective_mode == "gpu":
        detector_model = detector_model or "cnn"
        encoding_model = encoding_model or "large"
        num_jitters = num_jitters if num_jitters is not None else 3
    else:
        detector_model = detector_model or "hog"
        encoding_model = encoding_model or "small"
        num_jitters = num_jitters if num_jitters is not None else 1

    names: List[str] = []
    encodings: List[List[float]] = []

    training_files = list(_iter_image_files(TRAINING_DIR))
    total_files = len(training_files)

    for index, filepath in enumerate(training_files, start=1):
        name = filepath.parent.name
        image = face_recognition.load_image_file(filepath)

        face_locations = face_recognition.face_locations(image, model=detector_model)
        if not face_locations:
            continue

        face_encodings = face_recognition.face_encodings(
            image,
            known_face_locations=face_locations,
            num_jitters=num_jitters,
            model=encoding_model,
        )

        for encoding in face_encodings:
            names.append(name)
            encodings.append(encoding)

        if verbose:
            processed = len(encodings)
            sys.stdout.write(
                f"Encoding {index}/{total_files} files | {processed} embeddings collected\r"
            )
            sys.stdout.flush()

    if verbose and total_files:
        print()

    if not encodings:
        raise RuntimeError("No encodings generated. Improve dataset quality or detector settings.")

    with encodings_location.open(mode="wb") as handle:
        pickle.dump({"names": names, "encodings": encodings}, handle)

    clear_encodings_cache()


def _recognize_face(
    unknown_encoding,
    loaded_encodings,
    tolerance: float = 0.45,
) -> Tuple[Optional[str], Optional[float]]:
    comparisons = face_recognition.compare_faces(
        loaded_encodings["encodings"], unknown_encoding, tolerance=tolerance
    )
    votes = Counter(
        name
        for match, name in zip(comparisons, loaded_encodings["names"])
        if match
    )

    distances = face_recognition.face_distance(
        loaded_encodings["encodings"], unknown_encoding
    )
    if len(distances) == 0:
        return None, None

    if votes:
        winner = votes.most_common(1)[0][0]
        winner_distances = [
            distance
            for distance, candidate in zip(distances, loaded_encodings["names"])
            if candidate == winner
        ]
        best_distance = float(min(winner_distances)) if winner_distances else float(min(distances))
        return winner, best_distance

    best_index, best_distance = min(enumerate(distances), key=lambda pair: pair[1])
    best_distance = float(best_distance)
    if best_distance <= tolerance:
        return loaded_encodings["names"][best_index], best_distance
    return None, None


def _display_face(draw, bounding_box, name):
    top, right, bottom, left = bounding_box
    draw.rectangle(((left, top), (right, bottom)), outline=BOUNDING_BOX_COLOR)
    text_left, text_top, text_right, text_bottom = draw.textbbox(
        (left, bottom), name
    )
    draw.rectangle(
        ((text_left, text_top), (text_right, text_bottom)),
        fill="blue",
        outline="blue",
    )
    draw.text(
        (text_left, text_top),
        name,
        fill="white",
    )


def match_face(
    image_bytes: bytes,
    encodings_location: Path = DEFAULT_ENCODINGS_PATH,
    detector_model: str = "hog",
    encoding_model: str = "small",
    tolerance: float = 0.45,
):
    if not image_bytes:
        raise ValueError("Image payload is empty.")

    loaded_encodings = load_encodings(encodings_location)

    image_stream = io.BytesIO(image_bytes)
    image = face_recognition.load_image_file(image_stream)

    face_locations = face_recognition.face_locations(image, model=detector_model)
    if not face_locations:
        return None

    face_encodings = face_recognition.face_encodings(
        image,
        known_face_locations=face_locations,
        model=encoding_model,
    )

    for bounding_box, face_encoding in zip(face_locations, face_encodings):
        name, distance = _recognize_face(
            face_encoding, loaded_encodings, tolerance=tolerance
        )
        if name:
            return {
                "name": name,
                "distance": distance,
                "location": bounding_box,
            }

    return None


def recognize_faces(
    image_location: str,
    detector_model: str = "hog",
    encodings_location: Path = DEFAULT_ENCODINGS_PATH,
    encoding_model: str = "small",
    tolerance: float = 0.45,
) -> None:
    loaded_encodings = load_encodings(encodings_location)
    input_image = face_recognition.load_image_file(image_location)

    input_face_locations = face_recognition.face_locations(
        input_image, model=detector_model
    )
    input_face_encodings = face_recognition.face_encodings(
        input_image,
        known_face_locations=input_face_locations,
        model=encoding_model,
    )

    pillow_image = Image.fromarray(input_image)
    draw = ImageDraw.Draw(pillow_image)

    for bounding_box, unknown_encoding in zip(
        input_face_locations, input_face_encodings
    ):
        name, _ = _recognize_face(
            unknown_encoding, loaded_encodings, tolerance=tolerance
        )
        if not name:
            name = "Unknown"
        _display_face(draw, bounding_box, name)

    del draw
    pillow_image.show()


def validate(
    detector_model: str = "hog",
    encoding_model: str = "small",
    tolerance: float = 0.45,
):
    for filepath in VALIDATION_DIR.rglob("*"):
        print(filepath.absolute())
        if filepath.is_file():
            recognize_faces(
                image_location=str(filepath.absolute()),
                detector_model=detector_model,
                encoding_model=encoding_model,
                tolerance=tolerance,
            )


if __name__ == "__main__":
    print("Encoding known faces...")
    encode_known_faces(mode="cpu", verbose=True)
    print("Validating...")
    validate()