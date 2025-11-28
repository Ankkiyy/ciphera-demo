import json
from pathlib import Path
import sys
from threading import Lock
from typing import Dict, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile

app = FastAPI(title="CiphERA Node 2", version="1.0.0")

USERS_DB_PATH = Path(__file__).resolve().parent / "users.json"
LOCK = Lock()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from face_model import clear_encodings_cache, match_face

def ensure_db() -> None:
    if not USERS_DB_PATH.exists():
        USERS_DB_PATH.write_text("{}", encoding="utf-8")



def load_users() -> Dict[str, Dict[str, object]]:
    ensure_db()
    with USERS_DB_PATH.open("r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except json.JSONDecodeError:
            data = {}
    return data


def save_users(data: Dict[str, Dict[str, object]]) -> None:
    with USERS_DB_PATH.open("w", encoding="utf-8") as handle:
        json.dump(data, handle)


@app.post("/register")
async def register_user(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    file: Optional[UploadFile] = File(None),
    middle_name: Optional[str] = Form(None),
    phone: str = Form(...),
    address_line1: str = Form(...),
    address_line2: Optional[str] = Form(None),
    city: str = Form(...),
    state: Optional[str] = Form(None),
    postal_code: str = Form(...),
    country: str = Form(...),
    name: Optional[str] = Form(None),
    classification: Optional[str] = Form(None),
    face_slug: Optional[str] = Form(None),
    sample_count: Optional[str] = Form(None),
):
    if file is not None:
        await file.read()

    # Normalize profile fields before persistence for consistency.
    first_name = first_name.strip()
    middle_name = (middle_name or "").strip() or None
    last_name = last_name.strip()
    phone = phone.strip()
    address_line1 = address_line1.strip()
    address_line2 = (address_line2 or "").strip() or None
    city = city.strip()
    state = (state or "").strip() or None
    postal_code = postal_code.strip()
    country = country.strip()

    if not all([first_name, last_name, phone, address_line1, city, postal_code, country]):
        raise HTTPException(status_code=400, detail="Missing required profile attributes.")

    full_name = (name or " ".join(filter(None, [first_name, middle_name, last_name]))).strip()

    try:
        classification_payload = json.loads(classification) if classification else None
    except json.JSONDecodeError:
        classification_payload = classification

    profile = {
        "first_name": first_name,
        "middle_name": middle_name,
        "last_name": last_name,
        "phone": phone,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "city": city,
        "state": state,
        "postal_code": postal_code,
        "country": country,
        "classification": classification_payload,
    }

    with LOCK:
        users = load_users()
        parsed_sample_count = None
        if sample_count and str(sample_count).isdigit():
            parsed_sample_count = int(sample_count)

        users[email] = {
            "name": full_name,
            "face_slug": face_slug,
            "sample_count": parsed_sample_count,
            "profile": profile,
        }
        save_users(users)

    return {"status": "stored", "user": email, "profile": profile}


@app.post("/verify-face")
async def verify_face(file: UploadFile = File(...)):
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image provided.")

    users = load_users()
    if not users:
        return {"verified": False, "reason": "no_enrollments"}

    try:
        clear_encodings_cache()
        match = match_face(image_bytes)
    except FileNotFoundError:
        return {"verified": False, "reason": "encodings_missing"}

    if not match:
        return {"verified": False, "reason": "no_match"}

    recognized_slug = match.get("name")
    for email, record in users.items():
        stored_slug = record.get("face_slug") or record.get("profile", {}).get("face_slug")
        if stored_slug and stored_slug == recognized_slug:
            return {
                "verified": True,
                "user": email,
                "distance": match.get("distance"),
                "profile": record.get("profile"),
            }

    return {
        "verified": False,
        "reason": "unregistered_match",
        "candidate": recognized_slug,
    }


@app.post("/classifier-lookup")
async def classifier_lookup(payload: Dict[str, Optional[str]] = Body(...)):
    label = (payload or {}).get("label")
    if not label:
        raise HTTPException(status_code=400, detail="Classifier label is required.")

    label = label.strip()
    users = load_users()
    matches = []

    for email, record in users.items():
        profile = record.get("profile") or {}
        classification = profile.get("classification")

        classifier_label = None
        probability = None
        if isinstance(classification, dict):
            classifier_label = classification.get("label")
            probability = classification.get("probability")
        elif isinstance(classification, str):
            classifier_label = classification

        if classifier_label and classifier_label.strip() == label:
            matches.append(
                {
                    "email": email,
                    "name": record.get("name"),
                    "profile": profile,
                    "probability": probability,
                }
            )

    return {"label": label, "count": len(matches), "matches": matches}


if __name__ == "__main__":
    try:
        import uvicorn  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover - optional server dependency
        raise RuntimeError("Uvicorn must be installed to run node services.") from exc
    uvicorn.run(app, host="0.0.0.0", port=8002)
