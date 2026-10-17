import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from jose import JWTError, jwt
from pwdlib import PasswordHash

load_dotenv()

JWT_ALGORITHM = "HS256"
JWT_SECRET = os.getenv("SECRET_KEY") or os.getenv("JWT_SECRET")
if not JWT_SECRET or len(JWT_SECRET) < 32 or JWT_SECRET == "replace-this-with-a-long-random-value":
    raise RuntimeError("Set SECRET_KEY to a unique random value of at least 32 characters in backend/.env")

PASSWORD_HASHER = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return PASSWORD_HASHER.verify(password, password_hash)


def create_access_token(user_id: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
    )
    return jwt.encode({"sub": user_id, "exp": expires}, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None
