import json
import os
import time
from typing import Dict, List, Optional

import jwt
import requests
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

SECRET = os.getenv("SECRET", "CIPHERA_KEY")

# Static verifier node configuration for demo purposes.
NODES: List[str] = [
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8002",
]
if not NODES:
    raise ValueError("No verifier nodes configured. Set NODES environment variable.")


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
    file: UploadFile = File(...),
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
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail={"message": "No image content received."})

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
                },
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

    return {
        "message": "Registration broadcast complete.",
        "results": results,
        "profile": {"name": full_name, "email": email, **profile},
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
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
