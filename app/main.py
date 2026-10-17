import os
import hashlib
import hmac
import json
import logging
import secrets
import smtplib
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import uuid4
from urllib.parse import quote, unquote, urlparse

import pymysql
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import EmailStr, ValidationError
from pymysql.cursors import DictCursor

from .database import MYSQL_DATABASE, MYSQL_HOST, MYSQL_PASSWORD, MYSQL_PORT, MYSQL_USER, connect_mysql, create_tables, ensure_database_exists, get_db
from .mailer import (
    send_institute_approval_email,
    send_institute_rejection_email,
    send_password_reset_otp,
)
from .mock_tests import generate_questions
from .pdf_text import OcrUnavailableError, PdfTextExtractionError, extract_pdf_text
from .schemas import (
    ActivityCreate,
    DepartmentCreate,
    DepartmentUpdate,
    HomeBannerUpdate,
    InstituteApprovalUpdate,
    LoginRequest,
    MockTestGenerateRequest,
    Password,
    PasswordResetConfirm,
    PasswordResetRequest,
    PaperCreate,
    PaperUpdate,
    Phone,
    ReaderRecommendationPreferences,
    RegistrationText,
    RegisterRequest,
    UserCreate,
    UserUpdate,
)
from .security import JWT_SECRET, create_access_token, decode_access_token, hash_password, verify_password

load_dotenv()

logger = logging.getLogger(__name__)
PASSWORD_RESET_COOLDOWN_SECONDS = 60
PASSWORD_RESET_EXPIRY_MINUTES = 10
PASSWORD_RESET_MAX_ATTEMPTS = 5

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()
STUDY_RESOURCE_DIR = (UPLOAD_DIR.parent / "study_resources").resolve()
INSTITUTE_DOCUMENT_DIR = (UPLOAD_DIR.parent / "institute_documents").resolve()
ADMIN_DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "admin"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_HOME_BANNER_IMAGE_BYTES = 5 * 1024 * 1024
MAX_HOME_BANNER_IMAGE_PIXELS = 20_000_000
MAX_HOME_BANNER_GIF_FRAMES = 120
HOME_BANNER_IMAGE_DIR = UPLOAD_DIR / "home-banner"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Campus-notes API", version="1.0.0")
origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:8081").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/admin", StaticFiles(directory=ADMIN_DASHBOARD_DIR, html=True), name="admin")


def validate_home_banner_image(contents: bytes) -> str:
    extensions = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}
    try:
        with Image.open(BytesIO(contents)) as image:
            image_format = image.format
            if image_format not in extensions:
                raise HTTPException(
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    "Choose a PNG, JPG/JPEG, GIF, or WebP banner image.",
                )
            if image.width * image.height > MAX_HOME_BANNER_IMAGE_PIXELS:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "Banner images must be 20 megapixels or smaller.",
                )
            if image_format == "GIF" and image.n_frames > MAX_HOME_BANNER_GIF_FRAMES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"Animated GIFs must contain {MAX_HOME_BANNER_GIF_FRAMES} frames or fewer.",
                )
            image.verify()
            return extensions[image_format]
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The selected file is not a valid image. Choose a PNG, JPG/JPEG, GIF, or WebP image.",
        ) from error


def home_banner_image_path(image_url: str | None) -> Path | None:
    prefix = "/uploads/home-banner/"
    if not image_url or not image_url.startswith(prefix):
        return None
    filename = image_url.removeprefix(prefix)
    if not filename or Path(filename).name != filename:
        return None
    path = (HOME_BANNER_IMAGE_DIR / filename).resolve()
    return path if path.is_relative_to(HOME_BANNER_IMAGE_DIR.resolve()) else None


def remove_home_banner_image(image_url: str | None) -> None:
    path = home_banner_image_path(image_url)
    if path:
        path.unlink(missing_ok=True)


def query_one(db, sql: str, params: tuple = ()):
    with db.cursor(DictCursor) as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


def query_all(db, sql: str, params: tuple = ()):
    with db.cursor(DictCursor) as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def execute(db, sql: str, params: tuple = ()) -> int:
    with db.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.rowcount


def save_stored_file(db, file_id: str, category: str, file_name: str, media_type: str, contents: bytes) -> None:
    packet_limit = query_one(db, "SELECT @@max_allowed_packet AS packet_limit")["packet_limit"]
    chunk_size = max(1024, min(4 * 1024 * 1024, packet_limit // 4))
    execute(
        db,
        """
        INSERT INTO stored_files (id, category, file_name, media_type, contents, created_at)
        VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (file_id, category, file_name, media_type, b""),
    )
    for offset in range(0, len(contents), chunk_size):
        execute(
            db,
            "UPDATE stored_files SET contents = CONCAT(contents, %s) WHERE id = %s",
            (contents[offset:offset + chunk_size], file_id),
        )


def stored_file_response(stored_file: dict, *, inline: bool = False) -> Response:
    file_name = Path(stored_file["file_name"]).name
    disposition = "inline" if inline else "attachment"
    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file_name)}"}
    return Response(
        content=stored_file["contents"],
        media_type=stored_file["media_type"],
        headers=headers,
    )


def safe_legacy_file(path: str | Path, directory: Path) -> Path | None:
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (directory / candidate).resolve()
    return resolved if resolved.is_relative_to(directory.resolve()) and resolved.is_file() else None


def migrate_legacy_file(
    db,
    path: Path,
    *,
    category: str,
    file_name: str,
    media_type: str,
    update_sql: str,
    update_params: tuple,
) -> None:
    file_id = str(uuid4())
    save_stored_file(db, file_id, category, file_name, media_type, path.read_bytes())
    execute(db, update_sql, (file_id, *update_params))
    db.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning("Could not remove migrated upload %s: %s", path, error)


def migrate_legacy_uploads() -> None:
    db = connect_mysql()
    try:
        for resource in query_all(
            db,
            "SELECT id, file_path, file_name FROM study_resources WHERE file_id IS NULL AND file_path IS NOT NULL",
        ):
            path = safe_legacy_file(resource["file_path"], STUDY_RESOURCE_DIR)
            if path:
                migrate_legacy_file(
                    db,
                    path,
                    category="study_resource",
                    file_name=resource["file_name"],
                    media_type="application/pdf",
                    update_sql="UPDATE study_resources SET file_id = %s, file_path = NULL WHERE id = %s",
                    update_params=(resource["id"],),
                )

        for table in ("profiles", "users", "institute_staff"):
            for account in query_all(
                db,
                f"SELECT id, institute_document_path, institute_document_name "
                f"FROM {table} WHERE institute_document_file_id IS NULL AND institute_document_path IS NOT NULL",
            ):
                path = safe_legacy_file(account["institute_document_path"], INSTITUTE_DOCUMENT_DIR)
                if path:
                    media_type = {
                        ".pdf": "application/pdf",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".png": "image/png",
                    }.get(path.suffix.lower(), "application/octet-stream")
                    migrate_legacy_file(
                        db,
                        path,
                        category="institute_document",
                        file_name=account.get("institute_document_name") or path.name,
                        media_type=media_type,
                        update_sql=(
                            f"UPDATE {table} SET institute_document_file_id = %s, "
                            "institute_document_path = NULL WHERE id = %s"
                        ),
                        update_params=(account["id"],),
                    )

        banner = query_one(
            db,
            "SELECT image_url FROM home_banners WHERE id = 1 AND image_file_id IS NULL AND image_url IS NOT NULL",
        )
        if banner:
            path = home_banner_image_path(banner["image_url"])
            if path and path.is_file():
                media_type = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }.get(path.suffix.lower(), "application/octet-stream")
                migrate_legacy_file(
                    db,
                    path,
                    category="home_banner",
                    file_name=path.name,
                    media_type=media_type,
                    update_sql=(
                        "UPDATE home_banners SET image_file_id = %s, image_url = %s "
                        "WHERE id = 1 AND image_file_id IS NULL"
                    ),
                    update_params=("/api/home-banner/image",),
                )

        for paper in query_all(
            db,
            "SELECT id, file_url FROM papers WHERE file_id IS NULL AND file_url IS NOT NULL",
        ):
            path_part = unquote(urlparse(paper["file_url"]).path)
            if not path_part.startswith("/uploads/"):
                continue
            filename = Path(path_part).name
            if not filename or filename == "sample-campus-notes.pdf":
                continue
            path = safe_legacy_file(filename, UPLOAD_DIR)
            if path:
                file_id = str(uuid4())
                save_stored_file(db, file_id, "paper_pdf", filename, "application/pdf", path.read_bytes())
                execute(
                    db,
                    "UPDATE papers SET file_id = %s, file_url = %s WHERE id = %s",
                    (file_id, f"/api/uploads/{file_id}", paper["id"]),
                )
                db.commit()
                try:
                    path.unlink(missing_ok=True)
                except OSError as error:
                    logger.warning("Could not remove migrated upload %s: %s", path, error)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def paper_upload_file_id(file_url: str | None) -> str | None:
    path = urlparse(file_url or "").path
    prefix = "/api/uploads/"
    if not path.startswith(prefix):
        return None
    file_id = path.removeprefix(prefix)
    return file_id if file_id and "/" not in file_id else None


def account_table(role: str) -> str:
    if role == "user":
        return "users"
    if role == "institute":
        return "institute_staff"
    if role in {"dept_admin", "super_admin"}:
        return "profiles"
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User account not found")


def account_by_id(db, account_id: str):
    identity = query_one(db, "SELECT role FROM account_emails WHERE account_id = %s", (account_id,))
    if not identity:
        return None
    return query_one(
        db,
        f"SELECT * FROM {account_table(identity['role'])} WHERE id = %s",
        (account_id,),
    )


def account_by_email(db, email: str):
    identity = query_one(db, "SELECT account_id FROM account_emails WHERE email = %s", (email.lower(),))
    return account_by_id(db, identity["account_id"]) if identity else None


def register_account_email(db, email: str, account_id: str, role: str) -> None:
    execute(
        db,
        "INSERT INTO account_emails (email, account_id, role) VALUES (%s, %s, %s)",
        (email.lower(), account_id, role),
    )


def user_json(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "username": user.get("username"),
        "name": user["name"],
        "role": user["role"],
        "approval_status": user.get("approval_status", "approved"),
        "rejection_reason": user.get("rejection_reason"),
        "institute_name": user.get("institute_name"),
        "college_name": user.get("college_name"),
        "course_department_id": user.get("course_department_id"),
        "phone": user.get("phone"),
        "designation": user.get("designation"),
        "institute_document_name": user.get("institute_document_name"),
        "has_institute_document": bool(
            user.get("institute_document_file_id") or user.get("institute_document_path")
        ),
        "department_id": user.get("department_id"),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


def paper_json(paper: dict) -> dict:
    return {
        "id": paper["id"],
        "subject": paper["subject"],
        "subject_code": paper.get("subject_code"),
        "department_id": paper["department_id"],
        "department_name": paper.get("department_name"),
        "semester": paper["semester"],
        "exam_type": paper["exam_type"],
        "year": paper["year"],
        "description": paper.get("description"),
        "file_url": paper.get("file_url"),
        "uploaded_by": paper.get("uploaded_by"),
        "uploaded_by_name": paper.get("uploaded_by_name"),
        "views": paper["views"],
        "downloads": paper["downloads"],
        "created_at": paper.get("created_at"),
        "updated_at": paper.get("updated_at"),
        "recommendation_reason": paper.get("recommendation_reason"),
    }


def study_resource_json(resource: dict) -> dict:
    return {
        "id": resource["id"],
        "title": resource["title"],
        "category": resource["category"],
        "description": resource.get("description"),
        "visibility": resource["visibility"],
        "file_name": resource["file_name"],
        "uploaded_by": resource["uploaded_by"],
        "uploaded_by_name": resource["uploaded_by_name"],
        "created_at": resource.get("created_at"),
        "file_url": f"/api/study-resources/{resource['id']}/file",
    }


PAPER_SELECT = """
    SELECT p.*, d.name AS department_name
    FROM papers AS p
    LEFT JOIN departments AS d ON d.id = p.department_id
"""


def require_user(
    request: Request,
    db=Depends(get_db),
) -> dict:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = account_by_id(db, user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User account not found")
    return user


def require_roles(user: dict, roles: set[str]) -> None:
    if user["role"] == "institute" and user.get("approval_status") != "approved":
        status_message = (
            "Institute account approval is pending"
            if user.get("approval_status") == "pending"
            else "Institute account is not approved"
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, status_message)
    if user["role"] not in roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")


def can_manage_department(user: dict, department_id: str) -> None:
    require_roles(user, {"dept_admin", "super_admin", "institute"})
    if user["role"] == "dept_admin" and user.get("department_id") != department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot manage another department")


def require_paper_owner(user: dict, paper: dict) -> None:
    if user["role"] in {"institute", "user"} and paper.get("uploaded_by") != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only manage papers you uploaded")


def commit_or_conflict(db) -> None:
    try:
        db.commit()
    except pymysql.err.IntegrityError as error:
        db.rollback()
        if error.args and error.args[0] == 1062:
            raise HTTPException(status.HTTP_409_CONFLICT, "A record with these details already exists") from error
        raise
    except Exception:
        db.rollback()
        raise


def password_reset_code_hash(email: str, otp: str) -> str:
    return hmac.new(
        JWT_SECRET.encode("utf-8"),
        f"{email}:{otp}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def ensure_demo_paper() -> None:
    connection = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=10,
    )
    try:
        department = query_one(connection, "SELECT id FROM departments WHERE code = %s", ("CSE",))
        if not department:
            execute(
                connection,
                """
                INSERT INTO departments (id, name, code, created_at, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (str(uuid4()), "Computer Science and Engineering", "CSE"),
            )
            department = query_one(connection, "SELECT id FROM departments WHERE code = %s", ("CSE",))
            department_id = department["id"]
        else:
            department_id = department["id"]

        execute(
            connection,
            """
            INSERT IGNORE INTO papers (
                id, subject, subject_code, department_id, semester, exam_type, year,
                description, file_url, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (
                "00000000-0000-4000-8000-000000000105",
                "Sample: Campus-notes Demo Paper",
                "DEMO-PDF-001",
                department_id,
                1,
                "Sample PDF",
                "2026",
                "A demo PDF for testing paper reading and download. Not an official exam paper.",
                "/uploads/sample-campus-notes.pdf",
            ),
        )
        execute(
            connection,
            "UPDATE papers SET file_url = COALESCE(file_url, %s) WHERE id = %s",
            ("/uploads/sample-campus-notes.pdf", "00000000-0000-4000-8000-000000000105"),
        )
        commit_or_conflict(connection)
    finally:
        connection.close()


def initialize_database() -> None:
    ensure_database_exists()
    create_tables()
    ensure_demo_paper()
    migrate_legacy_uploads()
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if bool(admin_email) != bool(admin_password):
        raise RuntimeError("Configure both ADMIN_EMAIL and ADMIN_PASSWORD, or leave both empty")
    if not admin_email:
        return

    connection = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=10,
    )
    try:
        existing = query_one(connection, "SELECT account_id FROM account_emails WHERE email = %s", (admin_email,))
        if existing:
            return
        profile_id = str(uuid4())
        execute(
            connection,
            """
            INSERT INTO profiles (id, email, name, password_hash, role, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 'super_admin', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                profile_id,
                admin_email,
                os.getenv("ADMIN_NAME", "Campus-notes Administrator"),
                hash_password(admin_password),
            ),
        )
        register_account_email(connection, admin_email, profile_id, "super_admin")
        commit_or_conflict(connection)
    finally:
        connection.close()


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version}


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db=Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name is required")

    email = payload.email.lower()
    if query_one(db, "SELECT email FROM account_emails WHERE email = %s", (email,)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    if not query_one(
        db,
        "SELECT id FROM departments WHERE id = %s",
        (payload.course_department_id,),
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found")

    profile_id = str(uuid4())
    execute(
        db,
        """
        INSERT INTO users (
            id, email, name, password_hash, role, approval_status, college_name,
            course_department_id, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            profile_id,
            email,
            name,
            hash_password(payload.password),
            "user",
            "approved",
            payload.college_name.strip(),
            payload.course_department_id,
        ),
    )
    register_account_email(db, email, profile_id, "user")
    execute(
        db,
        """
        INSERT INTO activity_logs (id, type, message, user_id, created_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (
            str(uuid4()),
            "registration",
            f"{name} registered a user account",
            profile_id,
        ),
    )
    commit_or_conflict(db)
    user = account_by_id(db, profile_id)
    return {
        "user": user_json(user),
        "session": {"access_token": create_access_token(profile_id), "token_type": "bearer"},
    }


@app.post("/api/auth/register/institute", status_code=status.HTTP_201_CREATED)
async def register_institute(
    email: Annotated[EmailStr, Form()],
    password: Annotated[Password, Form()],
    name: Annotated[RegistrationText, Form(max_length=160)],
    phone: Annotated[Phone, Form()],
    designation: Annotated[RegistrationText, Form(max_length=120)],
    institute_name: Annotated[RegistrationText, Form(max_length=200)],
    file: UploadFile = File(...),
    db=Depends(get_db),
):
    name = name.strip()
    phone = phone.strip()
    designation = designation.strip()
    institute_name = institute_name.strip()
    if not all((name, phone, designation, institute_name)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "All institute staff details are required")

    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    allowed_suffixes = {".pdf", ".jpg", ".jpeg", ".png"}
    if suffix not in allowed_suffixes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Supporting document must be a PDF, JPG, or PNG file")

    contents = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(contents) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Maximum supporting document size is {MAX_UPLOAD_MB} MB",
        )

    valid_content = (
        (suffix == ".pdf" and contents.startswith(b"%PDF"))
        or (suffix in {".jpg", ".jpeg"} and contents.startswith(b"\xff\xd8\xff"))
        or (suffix == ".png" and contents.startswith(b"\x89PNG\r\n\x1a\n"))
    )
    if not valid_content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Supporting document does not match its file type")

    email = email.lower()
    if query_one(db, "SELECT email FROM account_emails WHERE email = %s", (email,)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    profile_id = str(uuid4())
    document_file_id = str(uuid4())
    document_media_type = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }[suffix]
    try:
        save_stored_file(
            db,
            document_file_id,
            "institute_document",
            filename,
            document_media_type,
            contents,
        )
        execute(
            db,
            """
            INSERT INTO institute_staff (
                id, email, name, password_hash, role, approval_status, institute_name,
                phone, designation, institute_document_path, institute_document_name,
                institute_document_file_id, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, 'institute', 'pending', %s, %s, %s, NULL, %s, %s,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                profile_id,
                email,
                name,
                hash_password(password),
                institute_name,
                phone,
                designation,
                filename,
                document_file_id,
            ),
        )
        register_account_email(db, email, profile_id, "institute")
        execute(
            db,
            """
            INSERT INTO activity_logs (id, type, message, user_id, created_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            (
                str(uuid4()),
                "registration",
                f"{name} registered an institute staff account pending approval",
                profile_id,
            ),
        )
        commit_or_conflict(db)
    except Exception:
        db.rollback()
        raise

    user = account_by_id(db, profile_id)
    return {
        "user": user_json(user),
        "session": {"access_token": create_access_token(profile_id), "token_type": "bearer"},
    }


@app.post("/api/auth/login")
def login(payload: LoginRequest, db=Depends(get_db)):
    user = account_by_email(db, payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    execute(
        db,
        """
        INSERT INTO activity_logs (id, type, message, user_id, created_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (str(uuid4()), "login", f"{user['name']} logged in", user["id"]),
    )
    db.commit()
    return {
        "user": user_json(user),
        "session": {"access_token": create_access_token(user["id"]), "token_type": "bearer"},
    }


@app.post("/api/auth/password-reset/request")
def request_password_reset(payload: PasswordResetRequest, db=Depends(get_db)):
    email = payload.email.lower()
    account = account_by_email(db, email)
    execute(
        db,
        """
        INSERT IGNORE INTO password_reset_otps (
            email, user_id, code_hash, expires_at, resend_after, attempts, used_at
        ) VALUES (%s, NULL, NULL, NULL, UTC_TIMESTAMP(), 0, NULL)
        """,
        (email,),
    )
    reset = query_one(
        db,
        """
        SELECT *,
            resend_after > UTC_TIMESTAMP() AS cooldown_active,
            GREATEST(0, TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), resend_after)) AS cooldown_seconds
        FROM password_reset_otps
        WHERE email = %s
        FOR UPDATE
        """,
        (email,),
    )
    if reset["cooldown_active"]:
        db.commit()
        return {
            "message": "If an account exists for this email, a reset code has been sent.",
            "resend_after_seconds": max(1, reset["cooldown_seconds"]),
        }

    otp = f"{secrets.randbelow(1_000_000):06d}" if account else None
    execute(
        db,
        """
        UPDATE password_reset_otps
        SET user_id = %s,
            code_hash = %s,
            expires_at = IF(%s IS NULL, NULL, DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s MINUTE)),
            resend_after = DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND),
            attempts = 0,
            used_at = NULL
        WHERE email = %s
        """,
        (
            account["id"] if account else None,
            password_reset_code_hash(email, otp) if otp else None,
            otp,
            PASSWORD_RESET_EXPIRY_MINUTES,
            PASSWORD_RESET_COOLDOWN_SECONDS,
            email,
        ),
    )
    db.commit()

    if account and otp:
        try:
            send_password_reset_otp(email, account["name"], otp)
        except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as error:
            logger.exception("Unable to send password reset email")
            execute(
                db,
                """
                UPDATE password_reset_otps
                SET code_hash = NULL, expires_at = NULL, resend_after = UTC_TIMESTAMP()
                WHERE email = %s
                """,
                (email,),
            )
            db.commit()
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Password reset email is temporarily unavailable. Please try again later.",
            ) from error

    return {
        "message": "If an account exists for this email, a reset code has been sent.",
        "resend_after_seconds": PASSWORD_RESET_COOLDOWN_SECONDS,
    }


@app.post("/api/auth/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, db=Depends(get_db)):
    email = payload.email.lower()
    reset = query_one(
        db,
        """
        SELECT *, expires_at > UTC_TIMESTAMP() AS code_is_active
        FROM password_reset_otps
        WHERE email = %s
        FOR UPDATE
        """,
        (email,),
    )
    valid = bool(
        reset
        and reset["user_id"]
        and reset["code_hash"]
        and not reset["used_at"]
        and reset["code_is_active"]
        and reset["attempts"] < PASSWORD_RESET_MAX_ATTEMPTS
        and hmac.compare_digest(reset["code_hash"], password_reset_code_hash(email, payload.otp))
    )
    if not valid:
        if reset and reset["attempts"] < PASSWORD_RESET_MAX_ATTEMPTS:
            execute(
                db,
                "UPDATE password_reset_otps SET attempts = attempts + 1 WHERE email = %s",
                (email,),
            )
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset code")

    account = account_by_email(db, email)
    if not account:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account not found")
    execute(
        db,
        f"UPDATE {account_table(account['role'])} SET password_hash = %s WHERE id = %s",
        (hash_password(payload.new_password), reset["user_id"]),
    )
    execute(
        db,
        "UPDATE password_reset_otps SET used_at = UTC_TIMESTAMP() WHERE email = %s",
        (email,),
    )
    execute(
        db,
        """
        INSERT INTO activity_logs (id, type, message, user_id, created_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (str(uuid4()), "password_reset", "Password reset by email code", reset["user_id"]),
    )
    db.commit()
    return {"message": "Password reset successfully. Sign in with your new password."}


@app.post("/api/auth/logout")
def logout(user: dict = Depends(require_user), db=Depends(get_db)):
    execute(
        db,
        """
        INSERT INTO activity_logs (id, type, message, user_id, created_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (str(uuid4()), "logout", f"{user['name']} logged out", user["id"]),
    )
    db.commit()
    return {"message": "Logged out successfully"}


@app.get("/api/auth/me")
def current_user(user: dict = Depends(require_user)):
    return {"user": user_json(user)}


@app.post("/api/recommendations/preferences")
def save_recommendation_preferences(
    payload: ReaderRecommendationPreferences,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    if user["role"] != "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only Reader accounts can set these preferences")
    if user.get("college_name") or user.get("course_department_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Recommendation preferences have already been set")
    if not query_one(db, "SELECT id FROM departments WHERE id = %s", (payload.course_department_id,)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found")
    execute(
        db,
        """
        UPDATE users
        SET college_name = %s, course_department_id = %s
        WHERE id = %s AND college_name IS NULL AND course_department_id IS NULL
        """,
        (payload.college_name.strip(), payload.course_department_id, user["id"]),
    )
    commit_or_conflict(db)
    updated_user = account_by_id(db, user["id"])
    if not updated_user or not updated_user.get("college_name") or not updated_user.get("course_department_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Recommendation preferences have already been set")
    return {"user": user_json(updated_user)}


@app.get("/api/recommendations")
def list_recommendations(
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    if user["role"] != "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Recommendations are available for Reader accounts")
    if not user.get("college_name") or not user.get("course_department_id"):
        return []
    rows = query_all(
        db,
        """
        SELECT p.*, d.name AS department_name,
            CASE
                WHEN p.department_id = %s AND EXISTS (
                    SELECT 1 FROM institute_staff AS staff
                    WHERE staff.id = p.uploaded_by
                        AND LOWER(TRIM(staff.institute_name)) = LOWER(TRIM(%s))
                ) THEN 'At your college and for your course'
                WHEN p.department_id = %s THEN 'For your course'
                ELSE 'At your college'
            END AS recommendation_reason
        FROM papers AS p
        LEFT JOIN departments AS d ON d.id = p.department_id
        WHERE p.file_url IS NOT NULL
            AND (
                p.department_id = %s
                OR EXISTS (
                    SELECT 1 FROM institute_staff AS staff
                    WHERE staff.id = p.uploaded_by
                        AND LOWER(TRIM(staff.institute_name)) = LOWER(TRIM(%s))
                )
            )
        ORDER BY
            CASE
                WHEN p.department_id = %s AND EXISTS (
                    SELECT 1 FROM institute_staff AS staff
                    WHERE staff.id = p.uploaded_by
                        AND LOWER(TRIM(staff.institute_name)) = LOWER(TRIM(%s))
                ) THEN 0
                WHEN p.department_id = %s THEN 1
                ELSE 2
            END,
            p.created_at DESC
        LIMIT 20
        """,
        (
            user["course_department_id"],
            user["college_name"],
            user["course_department_id"],
            user["course_department_id"],
            user["college_name"],
            user["course_department_id"],
            user["college_name"],
            user["course_department_id"],
        ),
    )
    return [paper_json(paper) for paper in rows]


@app.get("/api/departments")
def list_departments(db=Depends(get_db)):
    return query_all(db, "SELECT * FROM departments ORDER BY name")


@app.get("/api/home-banner")
def get_home_banner(db=Depends(get_db)):
    return query_one(
        db,
        """
        SELECT title, description, button_label, image_url
        FROM home_banners
        WHERE id = 1 AND is_active = TRUE
        """,
    )


@app.get("/api/home-banner/image")
def get_home_banner_image(db=Depends(get_db)):
    banner = query_one(
        db,
        "SELECT image_file_id, image_url FROM home_banners WHERE id = 1 AND is_active = TRUE",
    )
    if not banner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Home banner image not found")
    if banner.get("image_file_id"):
        stored_file = query_one(
            db,
            "SELECT file_name, media_type, contents FROM stored_files WHERE id = %s AND category = %s",
            (banner["image_file_id"], "home_banner"),
        )
        if stored_file:
            return stored_file_response(stored_file, inline=True)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Home banner image not found")

    path = home_banner_image_path(banner.get("image_url"))
    if not path or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Home banner image not found")
    return FileResponse(path)


@app.put("/api/home-banner")
async def update_home_banner(
    title: str = Form(...),
    description: str = Form(...),
    button_label: str = Form(...),
    image: UploadFile | None = File(default=None),
    remove_image: bool = Form(default=False),
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin"})
    try:
        payload = HomeBannerUpdate(
            title=title,
            description=description,
            button_label=button_label,
        )
    except ValidationError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, error.errors()) from error

    if image and remove_image:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Choose a new image or remove the current image, not both.",
        )

    old_banner = query_one(db, "SELECT image_url, image_file_id FROM home_banners WHERE id = 1")
    old_image_url = old_banner.get("image_url") if old_banner else None
    old_image_file_id = old_banner.get("image_file_id") if old_banner else None
    new_image_url = None
    new_image_file_id = None
    new_image_data = None
    if image:
        contents = await image.read(MAX_HOME_BANNER_IMAGE_BYTES + 1)
        if len(contents) > MAX_HOME_BANNER_IMAGE_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "Banner images must be 5 MB or smaller.",
            )
        extension = validate_home_banner_image(contents)
        filename = f"banner-{uuid4().hex}.{extension}"
        media_type = {
            "jpg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }[extension]
        new_image_file_id = str(uuid4())
        new_image_url = f"/api/home-banner/image?v={new_image_file_id}"
        new_image_data = (new_image_file_id, filename, media_type, contents)

    try:
        if new_image_data:
            file_id, filename, media_type, contents = new_image_data
            save_stored_file(
                db,
                file_id,
                "home_banner",
                filename,
                media_type,
                contents,
            )
        if image or remove_image:
            execute(
                db,
                """
                INSERT INTO home_banners
                    (id, title, description, button_label, image_url, image_file_id, is_active)
                VALUES (1, %s, %s, %s, %s, %s, TRUE)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    description = VALUES(description),
                    button_label = VALUES(button_label),
                    image_url = VALUES(image_url),
                    image_file_id = VALUES(image_file_id),
                    is_active = TRUE
                """,
                (
                    payload.title.strip(),
                    payload.description.strip(),
                    payload.button_label.strip(),
                    new_image_url,
                    new_image_file_id,
                ),
            )
        else:
            execute(
                db,
                """
                INSERT INTO home_banners
                    (id, title, description, button_label, is_active)
                VALUES (1, %s, %s, %s, TRUE)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    description = VALUES(description),
                    button_label = VALUES(button_label),
                    is_active = TRUE
                """,
                (
                    payload.title.strip(),
                    payload.description.strip(),
                    payload.button_label.strip(),
                ),
            )
        if (image or remove_image) and old_image_file_id:
            execute(
                db,
                "DELETE FROM stored_files WHERE id = %s AND category = %s",
                (old_image_file_id, "home_banner"),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    if (image or remove_image) and old_image_url != new_image_url:
        remove_home_banner_image(old_image_url)
    return get_home_banner(db)


@app.delete("/api/home-banner")
def delete_home_banner(
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin"})
    banner = query_one(db, "SELECT image_url, image_file_id FROM home_banners WHERE id = 1")
    execute(
        db,
        "UPDATE home_banners SET is_active = FALSE, image_url = NULL, image_file_id = NULL WHERE id = 1",
    )
    if banner and banner.get("image_file_id"):
        execute(
            db,
            "DELETE FROM stored_files WHERE id = %s AND category = %s",
            (banner["image_file_id"], "home_banner"),
        )
    db.commit()
    if banner:
        remove_home_banner_image(banner.get("image_url"))
    return {"message": "Home banner deleted"}


@app.get("/api/departments/{department_id}")
def get_department(department_id: str, db=Depends(get_db)):
    department = query_one(db, "SELECT * FROM departments WHERE id = %s", (department_id,))
    if not department:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    return department


@app.post("/api/departments", status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin", "institute"})
    department_id = str(uuid4())
    execute(
        db,
        """
        INSERT INTO departments (id, name, code, created_at, updated_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (department_id, payload.name.strip(), payload.code.strip().upper()),
    )
    commit_or_conflict(db)
    return query_one(db, "SELECT * FROM departments WHERE id = %s", (department_id,))


@app.put("/api/departments/{department_id}")
def update_department(
    department_id: str,
    payload: DepartmentUpdate,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin"})
    if not query_one(db, "SELECT id FROM departments WHERE id = %s", (department_id,)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return query_one(db, "SELECT * FROM departments WHERE id = %s", (department_id,))
    updates = []
    values = []
    for key, value in changes.items():
        updates.append(f"`{key}` = %s")
        values.append(value.strip().upper() if key == "code" else value.strip() if value else value)
    values.append(department_id)
    execute(db, f"UPDATE departments SET {', '.join(updates)} WHERE id = %s", tuple(values))
    commit_or_conflict(db)
    return query_one(db, "SELECT * FROM departments WHERE id = %s", (department_id,))


@app.delete("/api/departments/{department_id}")
def delete_department(
    department_id: str,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin"})
    if not query_one(db, "SELECT id FROM departments WHERE id = %s", (department_id,)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    execute(
        db,
        """
        DELETE FROM account_emails
        WHERE role = 'dept_admin'
            AND account_id IN (
                SELECT id FROM profiles WHERE department_id = %s AND role = 'dept_admin'
            )
        """,
        (department_id,),
    )
    execute(db, "DELETE FROM profiles WHERE department_id = %s AND role = 'dept_admin'", (department_id,))
    execute(db, "DELETE FROM departments WHERE id = %s", (department_id,))
    db.commit()
    return {"message": "Department deleted"}


@app.get("/api/papers")
def list_papers(
    department: str | None = None,
    semester: int | None = Query(default=None, ge=1, le=12),
    examType: str | None = None,
    year: str | None = None,
    search: str | None = None,
    sortBy: str = "newest",
    db=Depends(get_db),
):
    conditions = []
    params = []
    if department:
        conditions.append("p.department_id = %s")
        params.append(department)
    if semester:
        conditions.append("p.semester = %s")
        params.append(semester)
    if examType:
        conditions.append("p.exam_type = %s")
        params.append(examType)
    if year:
        conditions.append("p.year = %s")
        params.append(year)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append("(p.subject LIKE %s OR p.subject_code LIKE %s OR p.description LIKE %s)")
        params.extend((pattern, pattern, pattern))
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sort_clause = {
        "newest": "p.created_at DESC",
        "oldest": "p.created_at ASC",
        "name": "p.subject ASC",
        "downloads": "p.downloads DESC",
    }.get(sortBy, "p.created_at DESC")
    rows = query_all(db, PAPER_SELECT + where_clause + f" ORDER BY {sort_clause}", tuple(params))
    return [paper_json(paper) for paper in rows]


STUDY_RESOURCE_CATEGORIES = {
    "Assignment",
    "Notes",
    "Important information",
    "Study guide",
}


def study_resource_college(user: dict) -> str:
    college_name = user.get("college_name") if user["role"] == "user" else user.get("institute_name")
    return college_name.strip() if isinstance(college_name, str) else ""


def get_visible_study_resource(db, resource_id: str, user: dict):
    resource = query_one(db, "SELECT * FROM study_resources WHERE id = %s", (resource_id,))
    if not resource:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Study resource not found")
    college_name = study_resource_college(user)
    is_public = resource["visibility"] == "public"
    same_college = (
        resource["visibility"] == "college"
        and college_name
        and (resource.get("college_name") or "").strip().casefold() == college_name.casefold()
    )
    if not is_public and not same_college:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Study resource not found")
    return resource


@app.get("/api/study-resources")
def list_study_resources(
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"user", "institute"})
    college_name = study_resource_college(user)
    if college_name:
        rows = query_all(
            db,
            """
            SELECT * FROM study_resources
            WHERE visibility = 'public'
                OR (
                    visibility = 'college'
                    AND LOWER(TRIM(college_name)) = LOWER(TRIM(%s))
                )
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (college_name,),
        )
    else:
        rows = query_all(
            db,
            "SELECT * FROM study_resources WHERE visibility = 'public' ORDER BY created_at DESC LIMIT 100",
        )
    return [study_resource_json(resource) for resource in rows]


@app.post("/api/study-resources", status_code=status.HTTP_201_CREATED)
async def create_study_resource(
    title: Annotated[str, Form(min_length=1, max_length=200)],
    category: Annotated[str, Form()],
    visibility: Annotated[str, Form()],
    description: Annotated[str | None, Form(max_length=2000)] = None,
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
    db=Depends(get_db),
):
    require_roles(user, {"user", "institute"})
    title = title.strip()
    description = description.strip() if description else None
    if not title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A title is required")
    if category not in STUDY_RESOURCE_CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose a valid study material type")
    if visibility not in {"college", "public"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose same-college or public sharing")
    college_name = study_resource_college(user)
    if visibility == "college" and not college_name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Add a college/university to your account before sharing with your college",
        )
    if file.content_type != "application/pdf" or Path(file.filename or "").suffix.lower() != ".pdf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Study materials must be uploaded as PDF files")
    contents = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(contents) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Maximum upload size is {MAX_UPLOAD_MB} MB")
    if not contents.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File content is not a valid PDF")

    resource_id = str(uuid4())
    file_name = Path(file.filename or "study-material.pdf").name
    try:
        file_id = str(uuid4())
        save_stored_file(db, file_id, "study_resource", file_name, "application/pdf", contents)
        execute(
            db,
            """
            INSERT INTO study_resources (
                id, title, category, description, visibility, college_name,
                file_path, file_id, file_name, uploaded_by, uploaded_by_name, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            (
                resource_id,
                title,
                category,
                description,
                visibility,
                college_name or None,
                file_id,
                file_name,
                user["id"],
                user["name"],
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    resource = query_one(db, "SELECT * FROM study_resources WHERE id = %s", (resource_id,))
    return study_resource_json(resource)


@app.get("/api/study-resources/{resource_id}/file")
def download_study_resource(
    resource_id: str,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"user", "institute"})
    resource = get_visible_study_resource(db, resource_id, user)
    if resource.get("file_id"):
        stored_file = query_one(
            db,
            "SELECT file_name, media_type, contents FROM stored_files WHERE id = %s AND category = %s",
            (resource["file_id"], "study_resource"),
        )
        if not stored_file:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Study material file is not available")
        return stored_file_response(stored_file)

    file_path = safe_legacy_file(resource.get("file_path") or "", STUDY_RESOURCE_DIR)
    if not file_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Study material file is not available")
    return FileResponse(file_path, media_type="application/pdf", filename=resource["file_name"])


@app.delete("/api/study-resources/{resource_id}")
def delete_study_resource(
    resource_id: str,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"user", "institute"})
    resource = query_one(db, "SELECT * FROM study_resources WHERE id = %s", (resource_id,))
    if not resource:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Study resource not found")
    if resource["uploaded_by"] != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete materials you uploaded")
    execute(db, "DELETE FROM study_resources WHERE id = %s", (resource_id,))
    if resource.get("file_id"):
        execute(
            db,
            "DELETE FROM stored_files WHERE id = %s AND category = %s",
            (resource["file_id"], "study_resource"),
        )
    db.commit()
    file_path = safe_legacy_file(resource.get("file_path") or "", STUDY_RESOURCE_DIR)
    if file_path:
        file_path.unlink(missing_ok=True)
    return {"message": "Study material deleted"}


@app.post("/api/mock-tests/generate")
def generate_mock_test(
    payload: MockTestGenerateRequest,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    if user["role"] != "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mock tests are available for Reader accounts")

    paper_ids = list(dict.fromkeys(payload.paper_ids))
    years = list(dict.fromkeys(payload.years))
    paper_placeholders = ", ".join(["%s"] * len(paper_ids))
    year_placeholders = ", ".join(["%s"] * len(years))
    conditions = [
        f"p.id IN ({paper_placeholders})",
        f"p.year IN ({year_placeholders})",
        "p.file_url IS NOT NULL",
    ]
    params = [*paper_ids, *years]
    if user.get("course_department_id"):
        conditions.append("p.department_id = %s")
        params.append(user["course_department_id"])
    papers = query_all(
        db,
        PAPER_SELECT
        + f" WHERE {' AND '.join(conditions)} ORDER BY p.created_at DESC",
        tuple(params),
    )
    if not papers or set(years) != {str(paper["year"]) for paper in papers}:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No uploaded PDF papers were found for the selected subject and years in your course.",
        )

    source_parts = []
    source_papers = []
    remaining_chars = 60000
    for paper_index, paper in enumerate(papers):
        if remaining_chars <= 0:
            break
        file_url = paper.get("file_url") or ""
        parsed_url = urlparse(file_url)
        path_part = unquote(parsed_url.path)
        if path_part.startswith("/api/uploads/"):
            file_id = Path(path_part).name
            stored_file = query_one(
                db,
                "SELECT contents FROM stored_files WHERE id = %s AND category = %s",
                (file_id, "paper_pdf"),
            )
            if not stored_file:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "A selected paper PDF could not be found.",
                )
            pdf_source = stored_file["contents"]
            if len(pdf_source) > MAX_UPLOAD_MB * 1024 * 1024:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "A selected paper PDF exceeds the upload size limit.",
                )
        else:
            filename = Path(path_part).name
            if not path_part.startswith("/uploads/") or not filename:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A selected paper has an invalid PDF location.")
            pdf_path = (UPLOAD_DIR / filename).resolve()
            if not pdf_path.is_relative_to(UPLOAD_DIR) or not pdf_path.is_file():
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A selected paper PDF could not be found.")
            if pdf_path.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "A selected paper PDF exceeds the upload size limit.")
            pdf_source = pdf_path
        try:
            papers_left = len(papers) - paper_index
            paper_char_limit = min(12000, max(1, remaining_chars // papers_left))
            text = extract_pdf_text(pdf_source, paper_char_limit)
        except OcrUnavailableError as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"Local OCR is not configured. {error}",
            ) from error
        except PdfTextExtractionError as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Could not read the PDF for {paper['subject']}: {error}",
            ) from error
        if text and remaining_chars > 0:
            excerpt = text[:remaining_chars]
            source_parts.append(
                f"Paper: {paper['subject']} | Department: {paper.get('department_name') or 'Unknown'} "
                f"| Year: {paper['year']}\n{excerpt}"
            )
            source_papers.append({"subject": paper["subject"], "year": paper["year"]})
            remaining_chars -= len(excerpt)

    source_text = "\n\n".join(source_parts)
    if not source_text:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "These PDFs do not contain readable text, even after OCR. Check that the scans are clear.",
        )

    questions = generate_questions(source_text, payload.question_count, years)
    return {
        "title": "Your AI mock test",
        "years": years,
        "subjects": list(dict.fromkeys(paper["subject"] for paper in papers)),
        "source_papers": source_papers,
        "questions": questions,
    }


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: str, db=Depends(get_db)):
    paper = query_one(db, PAPER_SELECT + " WHERE p.id = %s", (paper_id,))
    if not paper:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    return paper_json(paper)


@app.post("/api/papers/{paper_id}/views")
def increment_views(paper_id: str, db=Depends(get_db)):
    if execute(db, "UPDATE papers SET views = views + 1 WHERE id = %s", (paper_id,)) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    db.commit()
    return {"message": "Views incremented"}


@app.post("/api/papers/{paper_id}/downloads")
def increment_downloads(paper_id: str, db=Depends(get_db)):
    if execute(db, "UPDATE papers SET downloads = downloads + 1 WHERE id = %s", (paper_id,)) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    db.commit()
    return {"message": "Downloads incremented"}


@app.post("/api/papers", status_code=status.HTTP_201_CREATED)
def create_paper(
    payload: PaperCreate,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    can_manage_department(user, payload.department_id)
    if not query_one(db, "SELECT id FROM departments WHERE id = %s", (payload.department_id,)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    paper_id = str(uuid4())
    values = payload.model_dump()
    file_id = paper_upload_file_id(values["file_url"])
    if file_id and not query_one(
        db,
        "SELECT id FROM stored_files WHERE id = %s AND category = %s",
        (file_id, "paper_pdf"),
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Uploaded paper PDF is not available")
    execute(
        db,
        """
        INSERT INTO papers (
            id, subject, subject_code, department_id, semester, exam_type, year,
            description, file_url, file_id, uploaded_by, uploaded_by_name, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            paper_id,
            values["subject"],
            values["subject_code"],
            values["department_id"],
            values["semester"],
            values["exam_type"],
            values["year"],
            values["description"],
            values["file_url"],
            file_id,
            user["id"],
            user["name"],
        ),
    )
    commit_or_conflict(db)
    return paper_json(query_one(db, PAPER_SELECT + " WHERE p.id = %s", (paper_id,)))


@app.put("/api/papers/{paper_id}")
def update_paper(
    paper_id: str,
    payload: PaperUpdate,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    paper = query_one(db, "SELECT * FROM papers WHERE id = %s", (paper_id,))
    if not paper:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    require_paper_owner(user, paper)
    changes = payload.model_dump(exclude_unset=True)
    can_manage_department(user, changes.get("department_id") or paper["department_id"])
    if "department_id" in changes and not query_one(
        db, "SELECT id FROM departments WHERE id = %s", (changes["department_id"],)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    previous_file_id = paper.get("file_id")
    if "file_url" in changes:
        file_id = paper_upload_file_id(changes["file_url"])
        if file_id and not query_one(
            db,
            "SELECT id FROM stored_files WHERE id = %s AND category = %s",
            (file_id, "paper_pdf"),
        ):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Uploaded paper PDF is not available")
        changes["file_id"] = file_id
    if changes:
        updates = [f"`{key}` = %s" for key in changes]
        params = [*changes.values(), paper_id]
        execute(db, f"UPDATE papers SET {', '.join(updates)} WHERE id = %s", tuple(params))
        commit_or_conflict(db)
        if previous_file_id and previous_file_id != changes.get("file_id", previous_file_id):
            execute(
                db,
                "DELETE FROM stored_files WHERE id = %s AND category = %s",
                (previous_file_id, "paper_pdf"),
            )
            db.commit()
    return paper_json(query_one(db, PAPER_SELECT + " WHERE p.id = %s", (paper_id,)))


@app.delete("/api/papers/{paper_id}")
def delete_paper(
    paper_id: str,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    paper = query_one(db, "SELECT * FROM papers WHERE id = %s", (paper_id,))
    if not paper:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    require_paper_owner(user, paper)
    can_manage_department(user, paper["department_id"])
    execute(db, "DELETE FROM papers WHERE id = %s", (paper_id,))
    if paper.get("file_id"):
        execute(
            db,
            "DELETE FROM stored_files WHERE id = %s AND category = %s",
            (paper["file_id"], "paper_pdf"),
        )
    db.commit()
    return {"message": "Paper deleted"}


@app.get("/api/favorites")
def list_favorites(db=Depends(get_db), user: dict = Depends(require_user)):
    rows = query_all(db, "SELECT paper_id FROM favorites WHERE user_id = %s", (user["id"],))
    return [row["paper_id"] for row in rows]


@app.post("/api/favorites/{paper_id}")
def toggle_favorite(paper_id: str, db=Depends(get_db), user: dict = Depends(require_user)):
    if not query_one(db, "SELECT id FROM papers WHERE id = %s", (paper_id,)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    favorite = query_one(
        db,
        "SELECT id FROM favorites WHERE user_id = %s AND paper_id = %s",
        (user["id"], paper_id),
    )
    if favorite:
        execute(db, "DELETE FROM favorites WHERE id = %s", (favorite["id"],))
        db.commit()
        return {"favorited": False}
    execute(
        db,
        """
        INSERT INTO favorites (id, user_id, paper_id, created_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (str(uuid4()), user["id"], paper_id),
    )
    commit_or_conflict(db)
    return {"favorited": True}


@app.get("/api/users")
def list_users(
    department: str | None = None,
    role: str | None = None,
    approval_status: str | None = None,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin", "dept_admin"})
    conditions = []
    params = []
    if user["role"] == "dept_admin":
        conditions.append("department_id = %s")
        params.append(user.get("department_id"))
    elif department:
        conditions.append("department_id = %s")
        params.append(department)
    if role:
        conditions.append("role = %s")
        params.append(role)
    if approval_status:
        conditions.append("approval_status = %s")
        params.append(approval_status)
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = query_all(
        db,
        """
        SELECT * FROM (
            SELECT id, email, username, name, password_hash, role, approval_status,
                NULL AS rejection_reason,
                institute_name, phone, designation, institute_document_path,
                institute_document_name, institute_document_file_id,
                college_name, course_department_id,
                department_id, created_at, updated_at
            FROM users
            UNION ALL
            SELECT id, email, username, name, password_hash, role, approval_status,
                rejection_reason,
                institute_name, phone, designation, institute_document_path,
                institute_document_name, institute_document_file_id,
                NULL AS college_name, NULL AS course_department_id,
                department_id, created_at, updated_at
            FROM institute_staff
            UNION ALL
            SELECT id, email, username, name, password_hash, role, approval_status,
                NULL AS rejection_reason,
                institute_name, phone, designation, institute_document_path,
                institute_document_name, institute_document_file_id,
                NULL AS college_name, NULL AS course_department_id,
                department_id, created_at, updated_at
            FROM profiles
        ) AS accounts
        """ + where_clause + " ORDER BY created_at DESC",
        tuple(params),
    )
    return [user_json(profile) for profile in rows]


@app.get("/api/users/{user_id}/institute-document")
def get_institute_document(
    user_id: str,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin"})
    target = account_by_id(db, user_id)
    if not target or target["role"] != "institute":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Institute document not found")
    document_file_id = target.get("institute_document_file_id")
    if document_file_id:
        stored_file = query_one(
            db,
            "SELECT file_name, media_type, contents FROM stored_files WHERE id = %s AND category = %s",
            (document_file_id, "institute_document"),
        )
        if not stored_file:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Institute document not found")
        return stored_file_response(stored_file)

    stored_path = target.get("institute_document_path")
    if not stored_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Institute document not found")

    document_path = Path(stored_path).resolve()
    if document_path.parent != INSTITUTE_DOCUMENT_DIR or not document_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Institute document not found")
    media_type = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(document_path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        document_path,
        media_type=media_type,
        filename=Path(target.get("institute_document_name") or document_path.name).name,
    )


@app.post("/api/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin"})
    if payload.role not in {"dept_admin", "super_admin"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid user role")
    if payload.role == "dept_admin" and not payload.department_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Department is required for department admins")
    if query_one(db, "SELECT email FROM account_emails WHERE email = %s", (payload.email.lower(),)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    if payload.department_id and not query_one(
        db, "SELECT id FROM departments WHERE id = %s", (payload.department_id,)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    profile_id = str(uuid4())
    execute(
        db,
        """
        INSERT INTO profiles (
            id, email, username, name, password_hash, role, department_id, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            profile_id,
            payload.email.lower(),
            payload.username,
            payload.name.strip(),
            hash_password(payload.password),
            payload.role,
            payload.department_id,
        ),
    )
    register_account_email(db, payload.email, profile_id, payload.role)
    commit_or_conflict(db)
    return user_json(account_by_id(db, profile_id))


@app.put("/api/users/{user_id}/approval")
def update_institute_approval(
    user_id: str,
    payload: InstituteApprovalUpdate,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin"})
    target = account_by_id(db, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if target["role"] != "institute":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only institute accounts require approval")
    rejection_reason = (payload.rejection_reason or "").strip()
    if payload.approval_status == "rejected" and not rejection_reason:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A reason is required when rejecting an institute account",
        )
    if rejection_reason and len(rejection_reason) > 1000:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Rejection reason must be 1000 characters or fewer",
        )
    should_send_approval_email = (
        payload.approval_status == "approved"
        and target.get("approval_status") != "approved"
    )
    should_send_rejection_email = (
        payload.approval_status == "rejected"
        and target.get("approval_status") != "rejected"
    )
    execute(
        db,
        "UPDATE institute_staff SET approval_status = %s, rejection_reason = %s WHERE id = %s",
        (
            payload.approval_status,
            rejection_reason if payload.approval_status == "rejected" else None,
            user_id,
        ),
    )
    execute(
        db,
        """
        INSERT INTO activity_logs (id, type, message, user_id, created_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (
            str(uuid4()),
            "institute_approval",
            f"{target['name']}'s institute account was {payload.approval_status}",
            user["id"],
        ),
    )
    db.commit()
    result = user_json(account_by_id(db, user_id))
    result["approval_email_sent"] = None
    if should_send_approval_email:
        try:
            send_institute_approval_email(target["email"], target["name"], target.get("institute_name") or "")
            result["approval_email_sent"] = True
        except (OSError, RuntimeError, smtplib.SMTPException, ValueError):
            logger.exception("Institute account approved, but approval email delivery failed")
            result["approval_email_sent"] = False
    result["rejection_email_sent"] = None
    if should_send_rejection_email:
        try:
            send_institute_rejection_email(
                target["email"],
                target["name"],
                target.get("institute_name") or "",
                rejection_reason,
            )
            result["rejection_email_sent"] = True
        except (OSError, RuntimeError, smtplib.SMTPException, ValueError):
            logger.exception("Institute account rejected, but rejection email delivery failed")
            result["rejection_email_sent"] = False
    return result


@app.put("/api/users/{user_id}")
def update_user(
    user_id: str,
    payload: UserUpdate,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    target = account_by_id(db, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    changes = payload.model_dump(exclude_unset=True)
    if user["role"] == "user":
        if user["id"] != target["id"] or target["role"] != "user":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Readers can only edit their own account")
        allowed_changes = {"name", "email", "password"}
        if changes.keys() - allowed_changes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Readers cannot change these account fields")
    if user["role"] == "institute":
        if user["id"] != target["id"] or target["role"] != "institute":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Institute staff can only edit their own account")
        allowed_changes = {"name", "email", "institute_name", "phone", "designation", "password"}
        if changes.keys() - allowed_changes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Institute staff cannot change these account fields")
    elif user["role"] != "super_admin" and (
        user["id"] != target["id"] or target.get("department_id") != user.get("department_id")
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    if "role" in changes:
        require_roles(user, {"super_admin"})
        if target["role"] not in {"dept_admin", "super_admin"} or changes["role"] not in {"dept_admin", "super_admin"}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid user role")
    if "department_id" in changes and changes["department_id"] and not query_one(
        db, "SELECT id FROM departments WHERE id = %s", (changes["department_id"],)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    if "password" in changes:
        changes["password_hash"] = hash_password(changes.pop("password"))
    changes = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in changes.items()
    }
    if "email" in changes:
        changes["email"] = changes["email"].lower()
        existing_email = query_one(
            db,
            "SELECT account_id FROM account_emails WHERE email = %s",
            (changes["email"],),
        )
        if existing_email and existing_email["account_id"] != user_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    if changes:
        updates = [f"`{key}` = %s" for key in changes]
        try:
            execute(
                db,
                f"UPDATE {account_table(target['role'])} SET {', '.join(updates)} WHERE id = %s",
                tuple([*changes.values(), user_id]),
            )
            if "email" in changes:
                execute(
                    db,
                    "UPDATE account_emails SET email = %s WHERE account_id = %s",
                    (changes["email"], user_id),
                )
            if "role" in changes:
                execute(
                    db,
                    "UPDATE account_emails SET role = %s WHERE account_id = %s",
                    (changes["role"], user_id),
                )
            commit_or_conflict(db)
        except pymysql.err.IntegrityError as error:
            db.rollback()
            if "email" in changes and error.args and error.args[0] == 1062:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "An account with this email already exists",
                ) from error
            raise
    return user_json(account_by_id(db, user_id))


@app.delete("/api/users/{user_id}")
def delete_user(
    user_id: str,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    require_roles(user, {"super_admin"})
    target = account_by_id(db, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if target["role"] == "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete a super admin")
    execute(db, f"DELETE FROM {account_table(target['role'])} WHERE id = %s", (user_id,))
    execute(db, "DELETE FROM account_emails WHERE account_id = %s", (user_id,))
    db.commit()
    return {"message": "User deleted"}


@app.get("/api/activity")
def list_activity(
    limit: int = Query(default=50, ge=1, le=200),
    user_id: str | None = None,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    if user["role"] == "dept_admin":
        rows = query_all(
            db,
            "SELECT * FROM activity_logs WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user["id"], limit),
        )
    elif user_id:
        rows = query_all(
            db,
            "SELECT * FROM activity_logs WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )
    else:
        rows = query_all(db, "SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT %s", (limit,))
    return rows


@app.post("/api/activity", status_code=status.HTTP_201_CREATED)
def create_activity(
    payload: ActivityCreate,
    db=Depends(get_db),
    user: dict = Depends(require_user),
):
    activity_id = str(uuid4())
    execute(
        db,
        """
        INSERT INTO activity_logs (id, type, message, user_id, created_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (activity_id, payload.type, payload.message, user["id"]),
    )
    db.commit()
    return query_one(db, "SELECT * FROM activity_logs WHERE id = %s", (activity_id,))


@app.get("/api/stats")
def get_stats(db=Depends(get_db), user: dict = Depends(require_user)):
    require_roles(user, {"super_admin", "dept_admin"})
    paper_filter = ""
    params = ()
    if user["role"] == "dept_admin":
        paper_filter = " WHERE department_id = %s"
        params = (user.get("department_id"),)
    totals = query_one(
        db,
        f"""
        SELECT COUNT(*) AS total_papers,
               COALESCE(SUM(downloads), 0) AS total_downloads,
               COALESCE(SUM(views), 0) AS total_views
        FROM papers{paper_filter}
        """,
        params,
    )
    if user["role"] == "dept_admin":
        departments = query_all(
            db,
            "SELECT id, name, code FROM departments WHERE id = %s ORDER BY name",
            (user.get("department_id"),),
        )
    else:
        departments = query_all(db, "SELECT id, name, code FROM departments ORDER BY name")
    total_departments = len(departments)
    admin_count = query_one(db, "SELECT COUNT(*) AS count FROM profiles WHERE role = 'dept_admin'")["count"]
    papers_by_dept = query_all(
        db,
        f"""
        SELECT d.name AS department, d.code, COUNT(p.id) AS count
        FROM departments AS d LEFT JOIN papers AS p ON p.department_id = d.id
        {'WHERE d.id = %s' if user['role'] == 'dept_admin' else ''}
        GROUP BY d.id, d.name, d.code ORDER BY d.name
        """,
        params,
    )
    exam_types = ["Weekly Test 1", "Weekly Test 2", "CIA 1", "CIA 2", "End Semester"]
    exam_rows = query_all(
        db,
        f"""
        SELECT exam_type AS type, COUNT(*) AS count FROM papers
        {"WHERE department_id = %s AND " if user["role"] == "dept_admin" else "WHERE "}exam_type IN ({", ".join(["%s"] * len(exam_types))})
        GROUP BY exam_type
        """,
        (*params, *exam_types),
    )
    exam_counts = {row["type"]: row["count"] for row in exam_rows}
    semester_rows = query_all(
        db,
        f"""
        SELECT semester, COUNT(*) AS count FROM papers
        {paper_filter}
        GROUP BY semester
        """,
        params,
    )
    semester_counts = {row["semester"]: row["count"] for row in semester_rows}
    recent_rows = query_all(
        db,
        PAPER_SELECT + paper_filter.replace("department_id", "p.department_id") + " ORDER BY p.created_at DESC LIMIT 10",
        params,
    )
    return {
        "totalPapers": totals["total_papers"],
        "totalDepartments": total_departments,
        "totalAdmins": admin_count,
        "totalDownloads": totals["total_downloads"],
        "totalViews": totals["total_views"],
        "papersByDept": papers_by_dept,
        "papersByExam": [{"type": exam, "count": exam_counts.get(exam, 0)} for exam in exam_types],
        "papersBySemester": [
            {"semester": semester, "count": semester_counts.get(semester, 0)}
            for semester in range(1, 9)
        ],
        "recentUploads": [paper_json(paper) for paper in recent_rows],
    }


@app.post("/api/uploads", status_code=status.HTTP_201_CREATED)
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
    db=Depends(get_db),
):
    require_roles(user, {"dept_admin", "super_admin", "institute"})
    if file.content_type != "application/pdf" or Path(file.filename or "").suffix.lower() != ".pdf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only PDF files are allowed")
    contents = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(contents) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Maximum upload size is {MAX_UPLOAD_MB} MB")
    if not contents.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File content is not a valid PDF")
    file_name = Path(file.filename or "paper.pdf").name
    file_id = str(uuid4())
    save_stored_file(db, file_id, "paper_pdf", file_name, "application/pdf", contents)
    db.commit()
    return {
        "url": str(request.base_url).rstrip("/") + f"/api/uploads/{file_id}",
        "file_id": file_id,
        "file_name": file_name,
    }


@app.get("/api/uploads/{file_id}")
def get_uploaded_paper(file_id: str, db=Depends(get_db)):
    stored_file = query_one(
        db,
        "SELECT file_name, media_type, contents FROM stored_files WHERE id = %s AND category = %s",
        (file_id, "paper_pdf"),
    )
    if not stored_file:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper PDF not found")
    return stored_file_response(stored_file, inline=True)
