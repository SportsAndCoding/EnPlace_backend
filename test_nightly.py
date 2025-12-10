import logging
from datetime import date
from dotenv import load_dotenv

# Load .env BEFORE importing modules that need env vars
load_dotenv()

from modules.sse.aggregation.full_nightly_job import run_full_nightly_job

logging.basicConfig(level=logging.INFO)

result = run_full_nightly_job(date(2025, 12, 8))
print(result)