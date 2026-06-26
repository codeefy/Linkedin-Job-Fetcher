import os
import json
import time
import logging
import threading
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from main import scrape_linkedin_jobs

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Honey-Bunny API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------
# In-memory cache: { "Marketing Intern_India": { data, timestamp } }
# -------------------------------------------------------------------
CACHE = {}
CACHE_TTL_SECONDS = 60 * 60  # 1 hour — don't re-scrape same query within 1 hr
CACHE_FILE = "cache.json"
cache_lock = threading.Lock()


def _cache_key(role: str, location: str) -> str:
    return f"{role.strip().lower()}_{location.strip().lower()}"


def _load_cache_from_disk():
    """Load cache from disk on startup so cache survives server restarts."""
    global CACHE
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                CACHE = json.load(f)
            logging.info(f"Loaded {len(CACHE)} cached queries from disk")
        except Exception as e:
            logging.warning(f"Could not load cache: {e}")
            CACHE = {}


def _save_cache_to_disk():
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(CACHE, f)
    except Exception as e:
        logging.warning(f"Could not save cache: {e}")


def _get_from_cache(key: str):
    with cache_lock:
        entry = CACHE.get(key)
        if not entry:
            return None
        # Check if cache is still fresh
        if time.time() - entry["timestamp"] < CACHE_TTL_SECONDS:
            return entry["data"]
        # Expired — remove it
        del CACHE[key]
        return None


def _set_cache(key: str, data: list):
    with cache_lock:
        CACHE[key] = {
            "data": data,
            "timestamp": time.time()
        }
        _save_cache_to_disk()


# Load cache on startup
_load_cache_from_disk()


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "message": "🍯 Honey-Bunny API is running",
        "docs": "/docs",
        "endpoints": {
            "search": "/jobs?role=Product Analyst&location=India",
            "cached_queries": "/cache",
            "health": "/health"
        }
    }


@app.get("/health")
def health():
    return {"status": "ok", "cached_queries": len(CACHE)}


@app.get("/cache")
def get_cached_queries():
    """Returns list of all currently cached role+location combinations."""
    with cache_lock:
        result = []
        for key, entry in CACHE.items():
            age_minutes = int((time.time() - entry["timestamp"]) / 60)
            expires_in = max(0, int((CACHE_TTL_SECONDS - (time.time() - entry["timestamp"])) / 60))
            result.append({
                "query": key,
                "job_count": len(entry["data"]),
                "cached_minutes_ago": age_minutes,
                "expires_in_minutes": expires_in,
            })
        return result


@app.get("/jobs")
def get_jobs(
    role: str = Query(default="Product Analyst", description="Job role to search"),
    location: str = Query(default="India", description="Location to search in"),
):
    """
    Main endpoint. Checks cache first, scrapes LinkedIn if not cached.
    Scraping takes 5-10 minutes — cache means repeat searches are instant.
    """
    if not role or len(role.strip()) < 2:
        raise HTTPException(status_code=400, detail="Role must be at least 2 characters")

    key = _cache_key(role, location)

    # Check cache first
    cached = _get_from_cache(key)
    if cached:
        logging.info(f"Cache hit for '{role}' in '{location}' — returning {len(cached)} jobs")
        return {
            "source": "cache",
            "role": role,
            "location": location,
            "count": len(cached),
            "jobs": cached
        }

    # Not cached — scrape live
    logging.info(f"Cache miss — scraping LinkedIn for '{role}' in '{location}'")
    try:
        jobs = scrape_linkedin_jobs(role, location)
    except Exception as e:
        logging.error(f"Scraping failed: {e}")
        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")

    if not jobs:
        # Still cache the empty result to avoid hammering LinkedIn
        _set_cache(key, [])
        return {
            "source": "live",
            "role": role,
            "location": location,
            "count": 0,
            "jobs": []
        }

    _set_cache(key, jobs)
    logging.info(f"Scraped and cached {len(jobs)} jobs for '{role}'")

    return {
        "source": "live",
        "role": role,
        "location": location,
        "count": len(jobs),
        "jobs": jobs
    }
