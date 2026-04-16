import os

UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "dimitar.trajanov@finki.ukim.mk")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "1"))
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", str(2 * 1024 * 1024)))  # 2MB
SESSION_TTL = int(os.environ.get("SESSION_TTL", "1800"))  # 30 minutes
SCHOLARLY_ENABLED = os.environ.get("SCHOLARLY_ENABLED", "true").lower() == "true"
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "") or os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "")
FLASK_PORT = int(os.environ.get("FLASK_PORT", "5000"))
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", os.path.join(os.path.dirname(__file__), "projects"))
