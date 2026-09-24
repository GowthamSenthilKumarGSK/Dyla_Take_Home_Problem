import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

ANALYST_MODEL = os.getenv("ANALYST_MODEL", "gpt-4o-mini")
PLANNING_MODEL = os.getenv("PLANNING_MODEL", "gpt-4o")
AUDITOR_MODEL = os.getenv("AUDITOR_MODEL", "gpt-4o-mini")

SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "5"))
FETCH_TIMEOUT_SECONDS = int(os.getenv("FETCH_TIMEOUT_SECONDS", "15"))
