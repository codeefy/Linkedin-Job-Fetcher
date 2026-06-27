import logging
import sys
import json
import os
import platform
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup
import pandas as pd
import random
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Only keep jobs posted within this many days
MAX_DAYS_OLD = 30


def _is_recent(date_str: str, max_days: int) -> bool:
    try:
        posted = datetime.strptime(date_str, "%Y-%m-%d")
        return datetime.now() - posted <= timedelta(days=max_days)
    except (ValueError, TypeError):
        return True


def _extract_description(driver) -> str:
    try:
        desc_elem = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located(
                (By.CLASS_NAME, "show-more-less-html__markup")
            )
        )
        return desc_elem.text.strip()
    except Exception:
        return ""


def _get_driver():
    """
    Returns a Chrome WebDriver.
    - On Railway/Docker: uses system Google Chrome + chromedriver
    - Locally on Windows/Mac: uses webdriver-manager to auto-download
    - Locally on Linux (not Docker): uses chromedriver-autoinstaller
    """
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--dns-prefetch-disable")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    # --- Railway / Docker environment ---
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("CHROME_BIN"):
        chrome_bin = os.environ.get("CHROME_BIN", "/usr/bin/google-chrome-stable")
        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
        chrome_options.binary_location = chrome_bin
        service = Service(executable_path=chromedriver_path)
        logging.info(f"Using system Chrome at {chrome_bin}")
        return webdriver.Chrome(service=service, options=chrome_options)

    # --- Local Windows / Mac ---
    if platform.system() in ["Windows", "Darwin"]:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            logging.info("Using webdriver-manager for Chrome")
            return webdriver.Chrome(service=service, options=chrome_options)
        except ImportError:
            logging.warning("webdriver-manager not found, falling back to autoinstaller")

    # --- Local Linux / fallback ---
    import chromedriver_autoinstaller
    chromedriver_autoinstaller.install()
    logging.info("Using chromedriver-autoinstaller")
    return webdriver.Chrome(options=chrome_options)


def scrape_linkedin_jobs(
    job_title: str,
    location: str,
    pages: int = 3,
    max_days_old: int = MAX_DAYS_OLD,
) -> list:

    driver = _get_driver()

    # Mask webdriver detection
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )

    url = (
        f"https://www.linkedin.com/jobs/search/?f_E=1&origin=JOB_SEARCH_PAGE_JOB_FILTER"
        f"&geoId=102713980&keywords={job_title}&location={location}&refresh=true&sortBy=DD"
    )
    driver.get(url)

    logging.info(f"Page title: {driver.title}")
    logging.info(f"Current URL: {driver.current_url}")

    time.sleep(random.choice(range(4, 8)))

    for i in range(pages):
        logging.info(f"Scrolling page {i + 1} of {pages}...")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        try:
            element = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, "/html/body/div[1]/div/main/section[2]/button")
                )
            )
            element.click()
        except Exception:
            logging.info("'Show more' button not found, continuing...")

        time.sleep(random.choice(range(3, 7)))

    jobs = []
    seen_links = set()
    soup = BeautifulSoup(driver.page_source, "html.parser")
    job_listings = soup.find_all(
        "div",
        class_="base-card relative w-full hover:no-underline focus:no-underline base-card--link base-search-card base-search-card--link job-search-card",
    )

    logging.info(f"Found {len(job_listings)} total listings — filtering...")

    try:
        for job in job_listings:
            title = job.find("h3", class_="base-search-card__title")
            company = job.find("h4", class_="base-search-card__subtitle")
            location_elem = job.find("span", class_="job-search-card__location")
            link_elem = job.find("a", class_="base-card__full-link")
            time_elem = job.find("time")

            if not all([title, company, location_elem, link_elem, time_elem]):
                continue

            title_text = title.text.strip()
            company_text = company.text.strip()
            location_text = location_elem.text.strip()
            apply_link = link_elem["href"]
            date_posted = time_elem.get("datetime", "")

            if not _is_recent(date_posted, max_days_old):
                logging.info(f'Skipping stale posting: "{title_text}" ({date_posted})')
                continue

            clean_link = apply_link.split("?")[0]
            if clean_link in seen_links:
                continue
            seen_links.add(clean_link)

            driver.get(apply_link)
            time.sleep(random.choice(range(5, 11)))
            description = _extract_description(driver)

            jobs.append(
                {
                    "Company": company_text,
                    "Title": title_text,
                    "Location": location_text,
                    "Link": f"[Apply]({apply_link})",
                    "Date Posted": date_posted,
                    "Description": description[:300] + "..." if len(description) > 300 else description,
                    "Search Role": job_title,
                }
            )

            logging.info(f'Scraped "{title_text}" at {company_text} in {location_text}')

    except Exception as e:
        logging.error(f"Error while scraping: {e}")
        return jobs
    finally:
        driver.quit()

    return jobs


def save_job_data(data: list) -> None:
    if not data:
        logging.warning("No jobs to save.")
        return

    data = sorted(data, key=lambda x: x["Date Posted"], reverse=True)
    df = pd.DataFrame(data)

    with open("jobs.json", "w") as f:
        json.dump(data, f, indent=2)
    logging.info("Saved jobs.json")

    with open("README.md", "r") as f:
        readme_content = f.read()

    start = readme_content.find("<!--START_SECTION:workfetch-->")
    end = readme_content.find("<!--END_SECTION:workfetch-->")

    if start == -1 or end == -1:
        logging.error("Could not find workfetch section markers in README.md")
        return

    new_content = (
        f"{readme_content[:start]}"
        f"<!--START_SECTION:workfetch-->\n"
        f"{df.to_markdown(index=False)}\n"
        f"{readme_content[end:]}"
    )

    with open("README.md", "w") as f:
        f.write(new_content)

    logging.info(f"Saved {len(data)} jobs to README.md")


# Default roles scraped automatically by GitHub Actions every day
DEFAULT_ROLES = [
    "Product Analyst",
    "Product Analyst Intern",
    "Business Analyst",
    "Business Analyst Intern",
    "Data Analyst",
    "Data Analyst Intern",
    "Data Engineer",
    "Data Engineer Intern",
    "ML Engineer",
    "ML Engineer Intern",
    "Marketing Analyst",
    "Marketing Intern",
    "Finance Analyst",
    "HR Intern",
]


if __name__ == "__main__":

    if len(sys.argv) >= 2:
        job_title = sys.argv[1]
        location = sys.argv[2] if len(sys.argv) >= 3 else "India"
        logging.info(f"Running in CI mode: '{job_title}' in '{location}'")
        jobs = scrape_linkedin_jobs(job_title, location)
        save_job_data(jobs)

    else:
        print("\n🔍 WorkFetch Job Scraper")
        print("------------------------")
        print("Press Enter with no input to scrape ALL default roles\n")
        job_title = input("Enter job role to search (e.g. Marketing Intern): ").strip()
        location = input("Enter location (press Enter for 'India'): ").strip()

        if not location:
            location = "India"

        if not job_title:
            print(f"\nNo role entered — scraping all {len(DEFAULT_ROLES)} default roles...\n")
            all_jobs = []
            seen_global = set()

            for role in DEFAULT_ROLES:
                logging.info(f"--- Scraping: {role} ---")
                jobs = scrape_linkedin_jobs(role, location)
                for job in jobs:
                    clean_link = job["Link"].split("(")[-1].rstrip(")").split("?")[0]
                    if clean_link not in seen_global:
                        seen_global.add(clean_link)
                        all_jobs.append(job)

            logging.info(f"Total unique jobs across all roles: {len(all_jobs)}")
            save_job_data(all_jobs)

        else:
            print(f"\nSearching for '{job_title}' in '{location}'...\n")
            jobs = scrape_linkedin_jobs(job_title, location)
            save_job_data(jobs)