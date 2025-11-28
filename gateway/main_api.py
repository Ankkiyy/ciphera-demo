import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import jwt
import requests
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from face_recognition import encode_known_faces, slugify_name, store_face_samples

SECRET = os.getenv("SECRET", "CIPHERA_KEY")

# Static verifier node configuration for demo purposes.
NODES: List[str] = [
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8002",
]
if not NODES:
    raise ValueError("No verifier nodes configured. Set NODES environment variable.")

FACE_ENCODER_MODE = os.getenv("FACE_ENCODER_MODE", "cpu").lower()
if FACE_ENCODER_MODE not in {"cpu", "gpu"}:
    FACE_ENCODER_MODE = "cpu"

FACE_SAMPLES_REQUIRED = int(os.getenv("FACE_SAMPLES_REQUIRED", "10"))


app = FastAPI(title="CiphERA Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/register")
async def register_user(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    face_samples: Optional[List[UploadFile]] = File(None),
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
):
    sample_payloads: List[bytes] = []

    if face_samples:
        for sample in face_samples:
            payload = await sample.read()
            if payload:
                sample_payloads.append(payload)

    if not sample_payloads and file is not None:
        fallback = await file.read()
        if fallback:
            sample_payloads.append(fallback)

    if not sample_payloads:
        raise HTTPException(status_code=400, detail={"message": "At least one face sample is required."})

    if FACE_SAMPLES_REQUIRED and len(sample_payloads) < FACE_SAMPLES_REQUIRED:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Insufficient face samples provided. Expected at least {FACE_SAMPLES_REQUIRED}.",
                "received": len(sample_payloads),
            },
        )

    if FACE_SAMPLES_REQUIRED:
        sample_payloads = sample_payloads[:FACE_SAMPLES_REQUIRED]

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
        raise HTTPException(status_code=400, detail={"message": "Missing required profile attributes."})

    full_name = (name or " ".join(filter(None, [first_name, middle_name, last_name]))).strip()

    try:
        classification_payload = json.loads(classification) if classification else None
    except json.JSONDecodeError:
        classification_payload = classification

    person_slug = slugify_name(first_name, last_name, email)

    try:
        saved_paths = store_face_samples(person_slug, sample_payloads, replace=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Failed to persist face samples to the training dataset.",
                "reason": str(exc),
            },
        ) from exc

    def _refresh_encodings() -> None:
        encode_known_faces(mode=FACE_ENCODER_MODE, verbose=False)

    try:
        try:
            await asyncio.to_thread(_refresh_encodings)
        except AttributeError:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _refresh_encodings)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Failed to refresh face recognition encodings after enrollment.",
                "reason": str(exc),
            },
        ) from exc

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
        "face_slug": person_slug,
        "sample_count": len(sample_payloads),
    }

    results = []
    for node in NODES:
        try:
            response = requests.post(
                f"{node}/register",
                data={
                    "name": full_name,
                    "email": email,
                    "first_name": first_name,
                    "middle_name": middle_name or "",
                    "last_name": last_name,
                    "phone": phone,
                    "address_line1": address_line1,
                    "address_line2": address_line2 or "",
                    "city": city,
                    "state": state or "",
                    "postal_code": postal_code,
                    "country": country,
                    "classification": classification or "",
                    "face_slug": person_slug,
                    "sample_count": str(len(sample_payloads)),
                },
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            payload["node"] = node
            results.append(payload)
        except requests.RequestException as exc:
            results.append({"node": node, "error": str(exc)})

    stored_nodes = [result for result in results if result.get("status") == "stored"]
    if not stored_nodes:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Registration failed on all verifier nodes.",
                "results": results,
            },
        )

    training_summary = {
        "face_slug": person_slug,
        "samples_saved": len(saved_paths),
        "encoder_mode": FACE_ENCODER_MODE,
        "expected_samples": FACE_SAMPLES_REQUIRED,
    }

    return {
        "message": "Registration broadcast complete.",
        "results": results,
        "profile": {"name": full_name, "email": email, **profile},
        "training": training_summary,
    }


@app.post("/api/signin")
async def signin_user(file: UploadFile = File(...)):
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail={"message": "No image content received."})

    votes = []
    for node in NODES:
        try:
            response = requests.post(
                f"{node}/verify-face",
                files={
                    "file": (
                        file.filename or "face.jpg",
                        image_bytes,
                        file.content_type or "image/jpeg",
                    )
                },
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            payload["node"] = node
            votes.append(payload)
        except requests.RequestException as exc:
            votes.append({"node": node, "error": str(exc), "verified": False})

    positive_votes = [vote for vote in votes if vote.get("verified")]
    required_majority = (len(NODES) // 2) + 1

    if len(positive_votes) >= required_majority:
        primary_vote = positive_votes[0]
        user_email = primary_vote.get("user")
        profiles = [vote.get("profile") for vote in positive_votes if vote.get("profile")]
        aggregated_profile = None
        if profiles:
            aggregated_profile = {
                "sources": [vote.get("node") for vote in positive_votes],
                "entries": profiles,
            }
            if profiles[0].get("classification"):
                aggregated_profile["classification"] = profiles[0]["classification"]
        issued_at = int(time.time())
        payload = {
            "user": user_email,
            "nodes": [vote.get("node") for vote in positive_votes],
            "profile": aggregated_profile,
            "iat": issued_at,
            "exp": issued_at + 3600,
        }
        token = jwt.encode(payload, SECRET, algorithm="HS256")
        return {
            "authenticated": True,
            "token": token,
            "votes": votes,
            "profile": aggregated_profile,
        }

    return {"authenticated": False, "votes": votes}


@app.post("/api/signin/classifier")
async def signin_classifier(label: str = Body(..., embed=True)):
    classifier_label = (label or "").strip()
    if not classifier_label:
        raise HTTPException(status_code=400, detail={"message": "Classifier label required."})

    results = []
    aggregated: Dict[str, dict] = {}

    for node in NODES:
        try:
            response = requests.post(
                f"{node}/classifier-lookup",
                json={"label": classifier_label},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            payload["node"] = node
            results.append(payload)

            for match in payload.get("matches", []):
                email = match.get("email")
                key = email or f"{node}:{match.get('name')}"
                entry = {
                    "node": node,
                    "email": email,
                    "name": match.get("name"),
                    "profile": match.get("profile"),
                    "probability": match.get("probability"),
                }

                existing = aggregated.get(key)
                if existing:
                    existing["sources"].append(entry)
                else:
                    aggregated[key] = {
                        "email": email,
                        "name": match.get("name"),
                        "sources": [entry],
                        "profile": match.get("profile"),
                    }
        except requests.RequestException as exc:
            results.append({"node": node, "error": str(exc)})

    matches = list(aggregated.values())

    return {
        "label": classifier_label,
        "matches": matches,
        "results": results,
    }

if __name__ == "__main__":
    try:
        import uvicorn  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover - optional server dependency
        raise RuntimeError("Uvicorn must be installed to run the gateway API.") from exc
    uvicorn.run(app, host="0.0.0.0", port=8000)
