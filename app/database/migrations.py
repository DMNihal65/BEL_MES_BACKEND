from pony.orm import db_session
import psycopg2
from ..config.settings import settings

def run_migrations():
    """Run database migrations"""
    conn = psycopg2.connect(
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        host=settings.DB_HOST,
        port=settings.DB_PORT
    )
    conn.autocommit = True
    cursor = conn.cursor()

    try:
        # Create schema
        cursor.execute('CREATE SCHEMA IF NOT EXISTS document_management;')

        # Create tables
        cursor.execute('''CREATE TABLE IF NOT EXISTS document_management.doc_folders (
    id SERIAL PRIMARY KEY,
    parent_folder INTEGER NULL,
    folder_name VARCHAR(255) NOT NULL,
    folder_path VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT fk_doc_folders_parent
        FOREIGN KEY (parent_folder)
        REFERENCES document_management.doc_folders(id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS document_management.doc_types (
    id SERIAL PRIMARY KEY,
    type_name VARCHAR(255) UNIQUE NOT NULL,
    description TEXT,
    file_extensions JSONB NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS document_management.documents (
    id SERIAL PRIMARY KEY,
    folder_id INTEGER NOT NULL,
    part_number_id INTEGER NOT NULL, -- References Order table
    doc_type_id INTEGER NOT NULL,
    document_name VARCHAR(255) NOT NULL,
    description TEXT,
    minio_path VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    latest_version_id INTEGER,
    CONSTRAINT fk_documents_folder
        FOREIGN KEY (folder_id)
        REFERENCES document_management.doc_folders(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_documents_doc_type
        FOREIGN KEY (doc_type_id)
        REFERENCES document_management.doc_types(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_documents_latest_version
        FOREIGN KEY (latest_version_id)
        REFERENCES document_management.document_versions(id)
        ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS document_management.document_versions (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL,
    version_number VARCHAR(50) NOT NULL,
    minio_object_id VARCHAR(255) NOT NULL,
    file_size INTEGER NOT NULL,
    checksum VARCHAR(255) NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL,
    CONSTRAINT fk_versions_document
        FOREIGN KEY (document_id)
        REFERENCES document_management.documents(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS document_management.document_access_logs (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NULL,
    version_id INTEGER NULL,
    user_id INTEGER NOT NULL,
    action_type VARCHAR(50) NOT NULL,
    action_timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(50),
    CONSTRAINT fk_access_logs_document
        FOREIGN KEY (document_id)
        REFERENCES document_management.documents(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_access_logs_version
        FOREIGN KEY (version_id)
        REFERENCES document_management.document_versions(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_access_logs_user
        FOREIGN KEY (user_id)
        REFERENCES auth.users(id)
        ON DELETE CASCADE
);
''')

        print("Migration completed successfully")

    except Exception as e:
        print(f"Migration failed: {str(e)}")
        raise

    finally:
        cursor.close()
        conn.close() 