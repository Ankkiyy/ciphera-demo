import io
import json
from pathlib import Path
from threading import Lock
from typing import Dict

import face_recognition
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

app = FastAPI(title="CiphERA Node 1", version="1.0.0")

USERS_DB_PATH = Path(__file__).resolve().parent / "users.json"
LOCK = Lock()


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
    name: str = Form(...),
    email: str = Form(...),
    file: UploadFile = File(...),
):
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image provided.")

    image_stream = io.BytesIO(image_bytes)
    image = face_recognition.load_image_file(image_stream)
    encodings = face_recognition.face_encodings(image)
    if not encodings:
        raise HTTPException(status_code=400, detail="No recognizable face detected.")

    embedding = encodings[0]

    with LOCK:
        users = load_users()
        users[email] = {"name": name, "embedding": embedding.tolist()}
        save_users(users)

    return {"status": "stored", "user": email}


@app.post("/verify-face")
async def verify_face(file: UploadFile = File(...)):
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image provided.")

    image_stream = io.BytesIO(image_bytes)
    image = face_recognition.load_image_file(image_stream)
    encodings = face_recognition.face_encodings(image)
    if not encodings:
        return {"verified": False, "reason": "face_not_detected"}

    probe_embedding = encodings[0]

    users = load_users()
    if not users:
        return {"verified": False, "reason": "no_enrollments"}

    # Use Euclidean distance threshold as consensus criterion between embeddings.
    for email, record in users.items():
        known_embedding = np.array(record.get("embedding", []))
        if known_embedding.size == 0:
            continue
        distance = np.linalg.norm(known_embedding - probe_embedding)
        if distance <= 0.45:
            return {
                "verified": True,
                "user": email,
                "distance": float(distance),
            }

    return {"verified": False, "reason": "no_match"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
