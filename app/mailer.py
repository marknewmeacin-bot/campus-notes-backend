import os
import smtplib
from email.message import EmailMessage


def send_password_reset_otp(email: str, name: str, otp: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM_EMAIL", "").strip()
    if not host or not sender:
        raise RuntimeError("Password reset email is not configured. Set SMTP_HOST and SMTP_FROM_EMAIL.")

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    if username and not password:
        raise RuntimeError("SMTP_PASSWORD is required when SMTP_USERNAME is configured.")
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"}
    sender_name = os.getenv("SMTP_FROM_NAME", "Campus-notes").strip()

    message = EmailMessage()
    message["Subject"] = "Your Campus-notes password reset code"
    message["From"] = f"{sender_name} <{sender}>"
    message["To"] = email
    message.set_content(
        f"Hello {name},\n\n"
        f"Your Campus-notes password reset code is: {otp}\n\n"
        "This code expires in 10 minutes and can only be used once. "
        "If you did not request a password reset, you can ignore this email."
    )

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def send_institute_approval_email(email: str, name: str, institute_name: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM_EMAIL", "").strip()
    if not host or not sender:
        raise RuntimeError("Approval email is not configured. Set SMTP_HOST and SMTP_FROM_EMAIL.")

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    if username and not password:
        raise RuntimeError("SMTP_PASSWORD is required when SMTP_USERNAME is configured.")
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"}
    sender_name = os.getenv("SMTP_FROM_NAME", "Campus-notes").strip()

    message = EmailMessage()
    message["Subject"] = "Your Campus-notes institute account is approved"
    message["From"] = f"{sender_name} <{sender}>"
    message["To"] = email
    message.set_content(
        f"Hello {name},\n\n"
        f"Your institute staff account for {institute_name} has been approved by the Campus-notes admin.\n\n"
        "You can now sign in to Campus-notes and upload question papers and study materials.\n\n"
        "If you did not request this account, please contact the Campus-notes administrator."
    )

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def send_institute_rejection_email(email: str, name: str, institute_name: str, reason: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM_EMAIL", "").strip()
    if not host or not sender:
        raise RuntimeError("Rejection email is not configured. Set SMTP_HOST and SMTP_FROM_EMAIL.")

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    if username and not password:
        raise RuntimeError("SMTP_PASSWORD is required when SMTP_USERNAME is configured.")
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"}
    sender_name = os.getenv("SMTP_FROM_NAME", "Campus-notes").strip()

    message = EmailMessage()
    message["Subject"] = "Update on your Campus-notes institute account"
    message["From"] = f"{sender_name} <{sender}>"
    message["To"] = email
    message.set_content(
        f"Hello {name},\n\n"
        f"Your institute staff account request for {institute_name} was not approved by the Campus-notes admin.\n\n"
        f"Reason provided by the admin:\n{reason}\n\n"
        "If you believe this decision was made in error, please contact the Campus-notes administrator."
    )

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
