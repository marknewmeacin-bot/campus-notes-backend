from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, EmailStr, Field


def validate_password(value: str) -> str:
    if not any(character.isupper() for character in value):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(not character.isalnum() and not character.isspace() for character in value):
        raise ValueError("Password must contain at least one special symbol")
    return value


Password = Annotated[
    str,
    Field(min_length=8, max_length=128),
    AfterValidator(validate_password),
]


def validate_phone(value: str) -> str:
    phone = value.strip()
    if not phone.isascii() or not phone.isdigit() or len(phone) != 10:
        raise ValueError("Phone number must contain exactly 10 digits")
    return phone


Phone = Annotated[
    str,
    Field(min_length=10, max_length=10),
    AfterValidator(validate_phone),
]


def validate_required_text(value: str) -> str:
    value = value.strip()
    if len(value) < 2:
        raise ValueError("Must contain at least 2 non-space characters")
    if not any(character.isalnum() for character in value):
        raise ValueError("Must contain at least one letter or number")
    return value


RegistrationText = Annotated[
    str,
    Field(min_length=2),
    AfterValidator(validate_required_text),
]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")
    new_password: Password


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    name: Annotated[RegistrationText, Field(max_length=160)]
    college_name: Annotated[RegistrationText, Field(max_length=200)]
    course_department_id: str
    account_type: Literal["user"] = "user"


class ReaderRecommendationPreferences(BaseModel):
    college_name: Annotated[RegistrationText, Field(max_length=200)]
    course_department_id: str = Field(min_length=1, max_length=36)


class MockTestGenerateRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=500)
    years: list[str] = Field(min_length=1, max_length=10)
    question_count: int = Field(default=10, ge=1, le=30)


class HomeBannerUpdate(BaseModel):
    title: Annotated[RegistrationText, Field(max_length=120)]
    description: Annotated[RegistrationText, Field(max_length=350)]
    button_label: Annotated[RegistrationText, Field(max_length=40)]


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=32)


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    code: str | None = Field(default=None, min_length=1, max_length=32)


class PaperCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    subject_code: str | None = Field(default=None, max_length=80)
    department_id: str
    semester: int = Field(ge=1, le=12)
    exam_type: str = Field(min_length=1, max_length=80)
    year: str = Field(min_length=4, max_length=8)
    description: str | None = None
    file_url: str | None = Field(default=None, max_length=500)


class PaperUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    subject_code: str | None = Field(default=None, max_length=80)
    department_id: str | None = None
    semester: int | None = Field(default=None, ge=1, le=12)
    exam_type: str | None = Field(default=None, min_length=1, max_length=80)
    year: str | None = Field(default=None, min_length=4, max_length=8)
    description: str | None = None
    file_url: str | None = Field(default=None, max_length=500)


class UserCreate(BaseModel):
    email: EmailStr
    password: Password
    name: str = Field(min_length=1, max_length=160)
    username: str | None = Field(default=None, max_length=80)
    role: str = "dept_admin"
    department_id: str | None = None


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: EmailStr | None = None
    username: str | None = Field(default=None, max_length=80)
    institute_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: Phone | None = None
    designation: Annotated[RegistrationText, Field(max_length=120)] | None = None
    role: str | None = None
    department_id: str | None = None
    password: Password | None = None


class InstituteApprovalUpdate(BaseModel):
    approval_status: Literal["approved", "rejected"]
    rejection_reason: str | None = Field(default=None, max_length=1000)


class ActivityCreate(BaseModel):
    type: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
