import os

UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "references-checker@example.com")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "5"))
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", str(2 * 1024 * 1024)))  # 2MB
SESSION_TTL = int(os.environ.get("SESSION_TTL", "1800"))  # 30 minutes
SCHOLARLY_ENABLED = os.environ.get("SCHOLARLY_ENABLED", "true").lower() == "true"
FLASK_PORT = int(os.environ.get("FLASK_PORT", "5000"))
