import asyncio
from playwright.async_api import async_playwright
import os
import csv
import re
import pandas as pd
import aiohttp
import logging

# --- Excel Setup ---
EXCEL_PATH = "data/100Drugs.xlsx"

def normalize_name(name: str) -> str:
    """Convert name to uppercase, remove parentheses and punctuation."""
    name = str(name)
    name = re.sub(r"\(.*?\)", "", name)  # remove text in ()
    name = re.sub(r"[^\w\s]", "", name)  # remove punctuation
    return name.strip().upper()

drug_df = pd.read_excel(EXCEL_PATH)
TARGET_DRUGS = set(drug_df["Drug Name"].astype(str).map(normalize_name))

# --- Constants ---
DOMAIN = "https://www.accessdata.fda.gov"
BASE_URL = f"{DOMAIN}/scripts/cder/daf/index.cfm?event=browseByLetter.page&productLetter={{}}&ai=0"
LETTERS = [chr(i) for i in range(ord('A'), ord('Z') + 1)]

SAVE_DIR = os.path.join(os.getcwd(), "data/fda_downloads_100")
PDF_DIR = os.path.join(SAVE_DIR, "pdfs")
CSV_FILE = os.path.join(SAVE_DIR, "fda_all_tables.csv")

os.makedirs(PDF_DIR, exist_ok=True)

# --- Logging Setup ---
LOG_FILE = os.path.join(SAVE_DIR, "fda_scraper.log")
os.makedirs(SAVE_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    filemode='a',
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def extract_appl_no_from_href(href: str) -> str:
    match = re.search(r"ApplNo=(\d+)", href)
    return match.group(1).zfill(6) if match else "UNKNOWN"


def make_safe_folder_name(name):
    return re.sub(r"[^\w\-\.]", "_", name)


async def download_pdf(page, url, appl_no, drug_name):
    filename = os.path.basename(url.split("#")[0])
    safe_name = make_safe_folder_name(drug_name)
    drug_dir = os.path.join(PDF_DIR, f"{appl_no}_{safe_name}")
    os.makedirs(drug_dir, exist_ok=True)
    save_path = os.path.join(drug_dir, filename)

    if os.path.exists(save_path):
        return

    try:
        async with page.expect_download() as download_info:
            await page.evaluate("""(url) => {
                const a = document.createElement('a');
                a.href = url;
                a.download = '';
                document.body.appendChild(a);
                a.click();
                a.remove();
            }""", url)
        download = await download_info.value
        await download.save_as(save_path)
        logger.info(f"Downloaded PDF: {filename}")
    except Exception as e:
        logger.error(f"Failed to download PDF: {url} | {e}")


async def extract_all_tables(page, drug_name, appl_no, version, letter):
    tables = await page.query_selector_all("table")
    all_data = []

    for i, table in enumerate(tables):
        rows = await table.query_selector_all("tr")
        for row in rows:
            cells = await row.query_selector_all("th, td")
            row_data = []
            for cell in cells:
                link = await cell.query_selector("a")
                if link:
                    href = await link.get_attribute("href")
                    text = await link.inner_text()
                    if href and href.endswith(".pdf"):
                        full_url = href if href.startswith("http") else DOMAIN + href
                        await download_pdf(page, full_url, appl_no, drug_name)
                        row_data.append(f"{text} ({full_url})")
                    else:
                        row_data.append(text)
                else:
                    row_data.append(await cell.inner_text())
            if row_data:
                all_data.append([drug_name, appl_no, version, letter, f"Table{i+1}"] + row_data)

    return all_data


async def extract_and_download_pdfs(page, appl_no, drug_name):
    logger.info(f"Extracting PDFs for {drug_name} | Application: {appl_no}")
    try:
        links = await page.locator("a").all()
        for anchor in links:
            try:
                text = (await anchor.inner_text()).strip().lower()
                if "pdf" in text or ".pdf" in (await anchor.get_attribute("href") or ""):
                    href = await anchor.get_attribute("href")

                    if not href:
                        continue

                    if href.startswith("/"):
                        href = f"https://www.fda.gov{href}"
                    elif not href.startswith("http"):
                        continue

                    filename = href.split("/")[-1]
                    safe_name = f"{drug_name.replace(' ', '_')}_{appl_no}_{filename}"
                    save_path = os.path.join("data", "fda_drugs", "pdfs", safe_name)

                    if os.path.exists(save_path):
                        continue

                    logger.info(f"Downloading PDF: {href}")
                    async with aiohttp.ClientSession() as session:
                        async with session.get(href, timeout=aiohttp.ClientTimeout(total=30)) as response:
                            if response.status == 200:
                                with open(save_path, "wb") as f:
                                    f.write(await response.read())
                            else:
                                logger.warning(f"Failed to download {href} | Status: {response.status}")
            except Exception as e:
                logger.error(f"Error extracting/downloading individual PDF: {e}")
    except Exception as e:
        logger.error(f"Error parsing PDF links for {drug_name} ({appl_no}): {e}")


async def scrape_fda():
    all_data = []

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        for letter in LETTERS:
            url = BASE_URL.format(letter)
            logger.info(f"\n--- Visiting letter '{letter}' page: {url}")
            await page.goto(url)

            try:
                await page.wait_for_selector("table", timeout=5000)
            except:
                logger.warning(f"No table found for letter {letter}")
                continue

            drug_links = await page.query_selector_all("a[href*='event=overview.process']")
            logger.info(f"Found {len(drug_links)} drugs for letter '{letter}'")

            for index, link in enumerate(drug_links):
                raw_name = await link.inner_text()
                cleaned_name = raw_name.strip()
                norm_name = normalize_name(cleaned_name)

                if norm_name not in TARGET_DRUGS:
                    continue

                href = await link.get_attribute("href")
                if not href:
                    continue

                appl_no = extract_appl_no_from_href(href)
                overview_url = DOMAIN + href
                logger.info(f">> {letter} {index+1}/{len(drug_links)}: {cleaned_name} ({appl_no})")

                overview_page = await context.new_page()
                await overview_page.goto(overview_url)

                extracted = await extract_all_tables(overview_page, cleaned_name, appl_no, "Overview", letter)
                all_data.extend(extracted)

                await extract_and_download_pdfs(overview_page, appl_no, cleaned_name)

                try:
                    await overview_page.wait_for_selector("a[href*='event=drugDetails.process']", timeout=3000)
                    detail_links = await overview_page.query_selector_all("a[href*='event=drugDetails.process']")
                    logger.info(f"  Found {len(detail_links)} detail versions")

                    for detail in detail_links:
                        version_name = await detail.inner_text()
                        detail_href = await detail.get_attribute("href")
                        if not detail_href:
                            continue

                        detail_url = DOMAIN + detail_href
                        detail_page = await context.new_page()
                        await detail_page.goto(detail_url)

                        detail_data = await extract_all_tables(detail_page, cleaned_name, appl_no, version_name, letter)
                        all_data.extend(detail_data)

                        await extract_and_download_pdfs(detail_page, appl_no, cleaned_name)
                        await detail_page.close()
                except:
                    logger.warning(f"  No detail links found for {cleaned_name}")

                await overview_page.close()

        await browser.close()

    if all_data:
        os.makedirs(SAVE_DIR, exist_ok=True)
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["DrugName", "ApplNo", "Version", "Letter", "TableID"] +
                            [f"Col{i+1}" for i in range(max(len(row) - 5 for row in all_data))])
            writer.writerows(all_data)
        logger.info("Saved extracted data to CSV.")

    logger.info("FDA scraping completed and saved.")


if __name__ == "__main__":
    asyncio.run(scrape_fda())
