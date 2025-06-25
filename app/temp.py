from app.database.connection import connect_to_db
from app.models.document_management_v2 import DocumentTypeV2

try:
    connect_to_db()
    print("Database Binded Successfully")

    DocumentTypeV2.get()

except Exception as e:
    print(f"Error generating mapping: {e}")
    exit(1)