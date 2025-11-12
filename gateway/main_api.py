import json
import os
import time
from typing import List

import jwt
import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

SECRET = os.getenv("SECRET", "CIPHERA_KEY")
NODES_ENV = os.getenv(
    "NODES",
    "[\"http://127.0.0.1:8001\", \"http://127.0.0.1:8002\"]",
)


def load_nodes(raw: str) -> List[str]:
    try:
        nodes_list = json.loads(raw)
        if isinstance(nodes_list, list):
            return [str(node).strip() for node in nodes_list if str(node).strip()]
    except json.JSONDecodeError:
        pass
    return [segment.strip() for segment in raw.split(",") if segment.strip()]


NODES = load_nodes(NODES_ENV)
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
    name: str = Form(...),
    email: str = Form(...),
    file: UploadFile = File(...),
):
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail={"message": "No image content received."})

    results = []
    for node in NODES:
        try:
            response = requests.post(
                f"{node}/register",
                data={"name": name, "email": email},
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

    return {"message": "Registration broadcast complete.", "results": results}


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
        issued_at = int(time.time())
        payload = {
            "user": user_email,
            "nodes": [vote.get("node") for vote in positive_votes],
            "iat": issued_at,
            "exp": issued_at + 3600,
        }
        token = jwt.encode(payload, SECRET, algorithm="HS256")
        return {"authenticated": True, "token": token, "votes": votes}

    return {"authenticated": False, "votes": votes}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
