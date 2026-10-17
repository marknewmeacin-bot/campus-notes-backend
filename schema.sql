CREATE DATABASE IF NOT EXISTS campus_notes
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE campus_notes;

CREATE TABLE IF NOT EXISTS stored_files (
    id CHAR(36) PRIMARY KEY,
    category VARCHAR(32) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    media_type VARCHAR(127) NOT NULL,
    contents LONGBLOB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX ix_stored_files_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS departments (
    id CHAR(36) PRIMARY KEY,
    name VARCHAR(160) NOT NULL UNIQUE,
    code VARCHAR(32) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS profiles (
    id CHAR(36) PRIMARY KEY,
    email VARCHAR(254) NOT NULL UNIQUE,
    username VARCHAR(80) NULL UNIQUE,
    name VARCHAR(160) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(32) NOT NULL,
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS account_emails (
    email VARCHAR(254) PRIMARY KEY,
    account_id CHAR(36) NOT NULL UNIQUE,
    role VARCHAR(32) NOT NULL,
    INDEX ix_account_emails_id_role (account_id, role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS password_reset_otps (
    email VARCHAR(254) PRIMARY KEY,
    user_id CHAR(36) NULL,
    code_hash CHAR(64) NULL,
    expires_at DATETIME NULL,
    resend_after DATETIME NOT NULL,
    attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
    used_at DATETIME NULL,
    INDEX ix_password_reset_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS activity_logs (
    id CHAR(36) PRIMARY KEY,
    type VARCHAR(80) NOT NULL,
    message VARCHAR(500) NOT NULL,
    user_id CHAR(36) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX ix_activity_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS home_banners (
    id TINYINT UNSIGNED PRIMARY KEY,
    title VARCHAR(120) NOT NULL,
    description VARCHAR(350) NOT NULL,
    button_label VARCHAR(40) NOT NULL,
    image_url VARCHAR(500) NULL,
    image_file_id CHAR(36) NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO home_banners (id, title, description, button_label, is_active)
VALUES (
    1,
    'Every paper. One place.',
    'Find the right question paper and make your next study session count.',
    'Explore papers',
    TRUE
);

-- Demo records for development. The Campus-notes sample paper has a bundled demo PDF.
INSERT IGNORE INTO departments (id, name, code, created_at, updated_at)
VALUES
    ('00000000-0000-4000-8000-000000000001', 'Computer Science and Engineering', 'CSE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('00000000-0000-4000-8000-000000000002', 'Electronics and Communication', 'ECE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('00000000-0000-4000-8000-000000000003', 'Mechanical Engineering', 'MECH', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT IGNORE INTO papers (
    id, subject, subject_code, department_id, semester, exam_type, year, description, created_at, updated_at
)
SELECT
    '00000000-0000-4000-8000-000000000101',
    'Sample: Data Structures',
    'DEMO-CS201',
    d.id,
    3,
    'CIA 1',
    '2025',
    'Demo listing for app preview, no PDF is attached.',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
FROM departments AS d
WHERE d.code = 'CSE';

INSERT IGNORE INTO papers (
    id, subject, subject_code, department_id, semester, exam_type, year,
    description, file_url, created_at, updated_at
)
SELECT
    '00000000-0000-4000-8000-000000000105',
    'Sample: Campus-notes Demo Paper',
    'DEMO-PDF-001',
    d.id,
    1,
    'Sample PDF',
    '2026',
    'A demo PDF for testing paper reading and download. Not an official exam paper.',
    '/uploads/sample-campus-notes.pdf',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
FROM departments AS d
WHERE d.code = 'CSE';

INSERT IGNORE INTO papers (
    id, subject, subject_code, department_id, semester, exam_type, year, description, created_at, updated_at
)
SELECT
    '00000000-0000-4000-8000-000000000102',
    'Sample: Database Management Systems',
    'DEMO-CS301',
    d.id,
    5,
    'End Semester',
    '2024',
    'Demo listing for app preview, no PDF is attached.',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
FROM departments AS d
WHERE d.code = 'CSE';

INSERT IGNORE INTO papers (
    id, subject, subject_code, department_id, semester, exam_type, year, description, created_at, updated_at
)
SELECT
    '00000000-0000-4000-8000-000000000103',
    'Sample: Digital Electronics',
    'DEMO-EC201',
    d.id,
    3,
    'CIA 2',
    '2025',
    'Demo listing for app preview, no PDF is attached.',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
FROM departments AS d
WHERE d.code = 'ECE';

INSERT IGNORE INTO papers (
    id, subject, subject_code, department_id, semester, exam_type, year, description, created_at, updated_at
)
SELECT
    '00000000-0000-4000-8000-000000000104',
    'Sample: Engineering Thermodynamics',
    'DEMO-ME201',
    d.id,
    3,
    'Weekly Test 1',
    '2024',
    'Demo listing for app preview, no PDF is attached.',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
FROM departments AS d
WHERE d.code = 'MECH';
