import os
from collections.abc import Generator

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor

load_dotenv()

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")

missing_settings = [
    name for name, value in (
        ("MYSQL_HOST", MYSQL_HOST),
        ("MYSQL_USER", MYSQL_USER),
        ("MYSQL_DATABASE", MYSQL_DATABASE),
    ) if not value
]
if missing_settings:
    raise RuntimeError(f"Configure {', '.join(missing_settings)} in backend/.env")
if not MYSQL_DATABASE.replace("_", "").isalnum():
    raise RuntimeError("MYSQL_DATABASE may contain only letters, numbers, and underscores")


def connect_mysql(*, database: str | None = MYSQL_DATABASE):
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=10,
    )


def get_db() -> Generator[pymysql.connections.Connection, None, None]:
    connection = connect_mysql()
    try:
        yield connection
    finally:
        connection.close()


def ensure_database_exists() -> None:
    connection = connect_mysql(database=None)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.commit()
    finally:
        connection.close()


def create_tables() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS stored_files (
            id CHAR(36) PRIMARY KEY,
            category VARCHAR(32) NOT NULL,
            file_name VARCHAR(255) NOT NULL,
            media_type VARCHAR(127) NOT NULL,
            contents LONGBLOB NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX ix_stored_files_category (category)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS departments (
            id CHAR(36) PRIMARY KEY,
            name VARCHAR(160) NOT NULL UNIQUE,
            code VARCHAR(32) NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS profiles (
            id CHAR(36) PRIMARY KEY,
            email VARCHAR(254) NOT NULL UNIQUE,
            username VARCHAR(80) NULL UNIQUE,
            name VARCHAR(160) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(32) NOT NULL DEFAULT 'user',
            approval_status VARCHAR(20) NOT NULL DEFAULT 'approved',
            institute_name VARCHAR(200) NULL,
            phone VARCHAR(32) NULL,
            designation VARCHAR(120) NULL,
            institute_document_path VARCHAR(500) NULL,
            institute_document_name VARCHAR(255) NULL,
            institute_document_file_id CHAR(36) NULL,
            department_id CHAR(36) NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX ix_profiles_department (department_id),
            CONSTRAINT fk_profiles_department FOREIGN KEY (department_id)
                REFERENCES departments(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id CHAR(36) PRIMARY KEY,
            email VARCHAR(254) NOT NULL UNIQUE,
            username VARCHAR(80) NULL UNIQUE,
            name VARCHAR(160) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(32) NOT NULL DEFAULT 'user',
            approval_status VARCHAR(20) NOT NULL DEFAULT 'approved',
            institute_name VARCHAR(200) NULL,
            phone VARCHAR(32) NULL,
            designation VARCHAR(120) NULL,
            institute_document_path VARCHAR(500) NULL,
            institute_document_name VARCHAR(255) NULL,
            institute_document_file_id CHAR(36) NULL,
            college_name VARCHAR(200) NULL,
            course_department_id CHAR(36) NULL,
            department_id CHAR(36) NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS institute_staff (
            id CHAR(36) PRIMARY KEY,
            email VARCHAR(254) NOT NULL UNIQUE,
            username VARCHAR(80) NULL UNIQUE,
            name VARCHAR(160) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(32) NOT NULL DEFAULT 'institute',
            approval_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            rejection_reason VARCHAR(1000) NULL,
            institute_name VARCHAR(200) NULL,
            phone VARCHAR(32) NULL,
            designation VARCHAR(120) NULL,
            institute_document_path VARCHAR(500) NULL,
            institute_document_name VARCHAR(255) NULL,
            institute_document_file_id CHAR(36) NULL,
            department_id CHAR(36) NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS account_emails (
            email VARCHAR(254) PRIMARY KEY,
            account_id CHAR(36) NOT NULL UNIQUE,
            role VARCHAR(32) NOT NULL,
            INDEX ix_account_emails_id_role (account_id, role)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS password_reset_otps (
            email VARCHAR(254) PRIMARY KEY,
            user_id CHAR(36) NULL,
            code_hash CHAR(64) NULL,
            expires_at DATETIME NULL,
            resend_after DATETIME NOT NULL,
            attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
            used_at DATETIME NULL,
            INDEX ix_password_reset_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS papers (
            id CHAR(36) PRIMARY KEY,
            subject VARCHAR(200) NOT NULL,
            subject_code VARCHAR(80) NULL,
            department_id CHAR(36) NOT NULL,
            semester INT NOT NULL,
            exam_type VARCHAR(80) NOT NULL,
            year VARCHAR(8) NOT NULL,
            description TEXT NULL,
            file_url VARCHAR(500) NULL,
            file_id CHAR(36) NULL,
            uploaded_by CHAR(36) NULL,
            uploaded_by_name VARCHAR(160) NULL,
            views INT NOT NULL DEFAULT 0,
            downloads INT NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX ix_papers_department (department_id),
            INDEX ix_papers_subject (subject),
            INDEX ix_papers_subject_code (subject_code),
            CONSTRAINT fk_papers_department FOREIGN KEY (department_id)
                REFERENCES departments(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS favorites (
            id CHAR(36) PRIMARY KEY,
            user_id CHAR(36) NOT NULL,
            paper_id CHAR(36) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_favorite_user_paper (user_id, paper_id),
            INDEX ix_favorites_user (user_id),
            INDEX ix_favorites_paper (paper_id),
            CONSTRAINT fk_favorites_paper FOREIGN KEY (paper_id)
                REFERENCES papers(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS study_resources (
            id CHAR(36) PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            category VARCHAR(40) NOT NULL,
            description TEXT NULL,
            visibility VARCHAR(20) NOT NULL,
            college_name VARCHAR(200) NULL,
            file_path VARCHAR(500) NULL,
            file_id CHAR(36) NULL,
            file_name VARCHAR(255) NOT NULL,
            uploaded_by CHAR(36) NOT NULL,
            uploaded_by_name VARCHAR(160) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX ix_study_resources_visibility_college (visibility, college_name),
            INDEX ix_study_resources_uploader (uploaded_by),
            INDEX ix_study_resources_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS activity_logs (
            id CHAR(36) PRIMARY KEY,
            type VARCHAR(80) NOT NULL,
            message VARCHAR(500) NOT NULL,
            user_id CHAR(36) NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX ix_activity_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS home_banners (
            id TINYINT UNSIGNED PRIMARY KEY,
            title VARCHAR(120) NOT NULL,
            description VARCHAR(350) NOT NULL,
            button_label VARCHAR(40) NOT NULL,
            image_url VARCHAR(500) NULL,
            image_file_id CHAR(36) NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ]
    connection = connect_mysql()
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
            cursor.execute("SHOW COLUMNS FROM home_banners LIKE 'image_url'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE home_banners ADD COLUMN image_url VARCHAR(500) NULL AFTER button_label")
            cursor.execute("SHOW COLUMNS FROM home_banners LIKE 'image_file_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE home_banners ADD COLUMN image_file_id CHAR(36) NULL AFTER image_url")
            cursor.execute(
                """
                INSERT IGNORE INTO home_banners
                    (id, title, description, button_label, is_active)
                VALUES (1, %s, %s, %s, TRUE)
                """,
                (
                    "Every paper. One place.",
                    "Find the right question paper and make your next study session count.",
                    "Explore papers",
                ),
            )
            cursor.execute("SHOW COLUMNS FROM institute_staff LIKE 'rejection_reason'")
            if not cursor.fetchone():
                cursor.execute(
                    "ALTER TABLE institute_staff ADD COLUMN rejection_reason "
                    "VARCHAR(1000) NULL AFTER approval_status"
                )
            cursor.execute("SHOW COLUMNS FROM profiles LIKE 'approval_status'")
            if not cursor.fetchone():
                cursor.execute(
                    "ALTER TABLE profiles ADD COLUMN approval_status "
                    "VARCHAR(20) NOT NULL DEFAULT 'approved' AFTER role"
                )
            cursor.execute("SHOW COLUMNS FROM profiles LIKE 'institute_name'")
            if not cursor.fetchone():
                cursor.execute(
                    "ALTER TABLE profiles ADD COLUMN institute_name VARCHAR(200) NULL AFTER approval_status"
                )
            for column, definition in (
                ("phone", "VARCHAR(32) NULL AFTER institute_name"),
                ("designation", "VARCHAR(120) NULL AFTER phone"),
                ("institute_document_path", "VARCHAR(500) NULL AFTER designation"),
                ("institute_document_name", "VARCHAR(255) NULL AFTER institute_document_path"),
                ("institute_document_file_id", "CHAR(36) NULL AFTER institute_document_name"),
            ):
                cursor.execute(f"SHOW COLUMNS FROM profiles LIKE '{column}'")
                if not cursor.fetchone():
                    cursor.execute(f"ALTER TABLE profiles ADD COLUMN {column} {definition}")
            for table in ("users", "institute_staff"):
                for column, definition in (
                    ("institute_document_file_id", "CHAR(36) NULL AFTER institute_document_name"),
                ):
                    cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE '{column}'")
                    if not cursor.fetchone():
                        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN {column} {definition}")
            cursor.execute("SHOW COLUMNS FROM papers LIKE 'file_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE papers ADD COLUMN file_id CHAR(36) NULL AFTER file_url")
            cursor.execute("SHOW COLUMNS FROM study_resources LIKE 'file_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE study_resources ADD COLUMN file_id CHAR(36) NULL AFTER file_path")
            cursor.execute("SHOW COLUMNS FROM study_resources LIKE 'file_path'")
            file_path_column = cursor.fetchone()
            if file_path_column and file_path_column["Null"] == "NO":
                cursor.execute("ALTER TABLE study_resources MODIFY file_path VARCHAR(500) NULL")
            for column, definition in (
                ("college_name", "VARCHAR(200) NULL AFTER institute_document_name"),
                ("course_department_id", "CHAR(36) NULL AFTER college_name"),
            ):
                cursor.execute(f"SHOW COLUMNS FROM users LIKE '{column}'")
                if not cursor.fetchone():
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
            for table, role in (("users", "user"), ("institute_staff", "institute")):
                cursor.execute(
                    f"""
                    INSERT IGNORE INTO {table} (
                        id, email, username, name, password_hash, role, approval_status,
                        institute_name, phone, designation, institute_document_path,
                        institute_document_name, institute_document_file_id, department_id,
                        created_at, updated_at
                    )
                    SELECT id, email, username, name, password_hash, role, approval_status,
                        institute_name, phone, designation, institute_document_path,
                        institute_document_name, institute_document_file_id, department_id,
                        created_at, updated_at
                    FROM profiles WHERE role = %s
                    """,
                    (role,),
                )
            for table in ("profiles", "users", "institute_staff"):
                cursor.execute(
                    f"""
                    INSERT IGNORE INTO account_emails (email, account_id, role)
                    SELECT LOWER(email), id, role FROM {table}
                    """
                )
            for table in ("password_reset_otps", "papers", "favorites", "activity_logs"):
                cursor.execute(
                    """
                    SELECT CONSTRAINT_NAME
                    FROM information_schema.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = DATABASE()
                        AND TABLE_NAME = %s
                        AND REFERENCED_TABLE_NAME = 'profiles'
                    """,
                    (table,),
                )
                for constraint in cursor.fetchall():
                    cursor.execute(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{constraint['CONSTRAINT_NAME']}`")
            cursor.execute("DELETE FROM profiles WHERE role IN ('user', 'institute')")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
