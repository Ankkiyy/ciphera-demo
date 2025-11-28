import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import jwt
import requests
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from face_model import encode_known_faces, slugify_name, store_face_samples

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
async def register_user(request: Request):
    form_data = await request.form()

    def _required(field: str) -> str:
        value = form_data.get(field)
        if isinstance(value, str):
            value = value.strip()
        if not value:
            missing_fields.append(field)
            return ""
        return value

    def _optional(field: str) -> Optional[str]:
        value = form_data.get(field)
        if isinstance(value, str):
            value = value.strip()
        return value or None

    missing_fields: List[str] = []

    first_name = _required("first_name")
    last_name = _required("last_name")
    email = _required("email")
    phone = _required("phone")
    address_line1 = _required("address_line1")
    city = _required("city")
    postal_code = _required("postal_code")
    country = _required("country")

    middle_name = _optional("middle_name")
    address_line2 = _optional("address_line2")
    state = _optional("state")
    name = _optional("name")
    classification_raw = _optional("classification")

    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Missing required profile attributes.",
                "missing": missing_fields,
            },
        )

    sample_payloads: List[bytes] = []

    raw_candidates: List[object] = []
    if hasattr(form_data, "getlist"):
        for field_name in ("face_samples", "face_samples[]"):
            try:
                items = form_data.getlist(field_name)
            except (AttributeError, TypeError):
                items = None
            if items:
                raw_candidates.extend(items)

    if not raw_candidates:
        fallback_entry = form_data.get("face_samples")
        if fallback_entry is not None:
            raw_candidates.append(fallback_entry)

    if hasattr(form_data, "multi_items"):
        try:
            for key, value in form_data.multi_items():
                if key == "face_samples" or key.startswith("face_samples["):
                    raw_candidates.append(value)
        except (AttributeError, TypeError):
            pass

    seen_uploads: set[int] = set()
    for entry in raw_candidates:
        if isinstance(entry, UploadFile) or (hasattr(entry, "read") and hasattr(entry, "filename")):
            entry_id = id(entry)
            if entry_id in seen_uploads:
                continue
            seen_uploads.add(entry_id)
            try:
                await entry.seek(0)
            except (AttributeError, TypeError):
                try:
                    entry.file.seek(0)  # type: ignore[attr-defined]
                except Exception:
                    pass
            payload = await entry.read()
            if payload:
                sample_payloads.append(payload)
            await entry.close()
        elif isinstance(entry, (bytes, bytearray)):
            if entry:
                sample_payloads.append(bytes(entry))
        elif isinstance(entry, str) and entry:
            if entry.startswith("data:") and "," in entry:
                _, encoded = entry.split(",", 1)
                try:
                    decoded = base64.b64decode(encoded)
                except (base64.binascii.Error, ValueError):
                    decoded = b""
                if decoded:
                    sample_payloads.append(decoded)

    primary_file = form_data.get("file")
    if not sample_payloads and isinstance(primary_file, UploadFile):
        fallback = await primary_file.read()
        if fallback:
            sample_payloads.append(fallback)
        await primary_file.close()

    if not sample_payloads:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "At least one face sample is required.",
                "received_face_fields": len(raw_candidates),
            },
        )

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
        classification_payload = json.loads(classification_raw) if classification_raw else None
    except json.JSONDecodeError:
        classification_payload = classification_raw

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
                    "classification": classification_raw or "",
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

        sources: List[Optional[str]] = []
        entries: List[Dict[str, object]] = []
        classification_value: Optional[object] = None

        for vote in positive_votes:
            node_name = vote.get("node")
            if node_name:
                sources.append(node_name)
            profile_entry = vote.get("profile")
            if profile_entry is not None:
                entry_payload: Dict[str, object] = {"node": node_name, "profile": profile_entry}
                entries.append(entry_payload)
                if classification_value is None and isinstance(profile_entry, dict):
                    classification_candidate = profile_entry.get("classification")
                    if classification_candidate not in (None, ""):
                        classification_value = classification_candidate

        if sources:
            sources = list(dict.fromkeys(sources))

        aggregated_profile: Optional[Dict[str, object]] = None
        if entries:
            aggregated_profile = {
                "email": user_email,
                "sources": sources,
                "entries": entries,
            }
            if classification_value is not None:
                aggregated_profile["classification"] = classification_value

        distance_values = [
            float(vote.get("distance"))
            for vote in positive_votes
            if isinstance(vote.get("distance"), (int, float))
        ]

        metrics: Optional[Dict[str, object]] = None
        if distance_values:
            best_distance = min(distance_values)
            average_distance = sum(distance_values) / len(distance_values)
            confidence = max(0.0, min(1.0, 1.0 - best_distance))
            metrics = {
                "best_distance": round(best_distance, 4),
                "average_distance": round(average_distance, 4),
                "confidence": round(confidence, 4),
                "positive_votes": len(positive_votes),
                "required_majority": required_majority,
            }

        issued_at = int(time.time())
        payload = {
            "user": user_email,
            "nodes": [vote.get("node") for vote in positive_votes],
            "profile": aggregated_profile,
            "metrics": metrics,
            "iat": issued_at,
            "exp": issued_at + 3600,
        }
        token = jwt.encode(payload, SECRET, algorithm="HS256")
        response_payload = {
            "authenticated": True,
            "user": user_email,
            "token": token,
            "votes": votes,
            "profile": aggregated_profile,
            "metrics": metrics,
        }
        return response_payload

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
