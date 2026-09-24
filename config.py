import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

ANALYST_MODEL = os.getenv("ANALYST_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
PLANNING_MODEL = os.getenv("PLANNING_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
AUDITOR_MODEL = os.getenv("AUDITOR_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "google/gemma-4-31b-it:free")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "5"))
FETCH_TIMEOUT_SECONDS = int(os.getenv("FETCH_TIMEOUT_SECONDS", "15"))
