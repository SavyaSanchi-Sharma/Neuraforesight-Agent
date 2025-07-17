import asyncio
from playwright.async_api import async_playwright
import os
import csv
import re
import aiohttp

DOMAIN = "https://www.accessdata.fda.gov"
BASE_URL = f"{DOMAIN}/scripts/cder/daf/index.cfm?event=browseByLetter.page&productLetter={{}}&ai=0"
LETTERS = [chr(i) for i in range(ord('A'), ord('Z') + 1)]

SAVE_DIR = os.path.join(os.getcwd(), "fda_downloads")
PDF_DIR = os.path.join(SAVE_DIR, "pdfs")
CSV_FILE = os.path.join(SAVE_DIR, "fda_all_tables.csv")

os.makedirs(PDF_DIR, exist_ok=True)


def extract_appl_no_from_href(href: str) -> str:
    match = re.search(r"ApplNo=(\d+)", href)
    if match:
        return match.group(1).zfill(6)
    return "UNKNOWN"


def make_safe_folder_name(name):
    return re.sub(r"[^\w\-\.]", "_", name)


async def download_pdf(page, url, appl_no, drug_name):
    filename = os.path.basename(url.split("#")[0])
    safe_name = make_safe_folder_name(drug_name)
    drug_dir = os.path.join(PDF_DIR, f"{appl_no}_{safe_name}")
    os.makedirs(drug_dir, exist_ok=True)
    save_path = os.path.join(drug_dir, filename)

    if os.path.exists(save_path):
        return  # already downloaded

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
        print(f"Downloaded PDF: {filename}")
    except Exception as e:
        print(f"Failed to download PDF: {url} | {e}")


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
    print(f"Extracting PDFs for {drug_name} | Application: {appl_no}")
    try:
        links = await page.locator("a").all()
        for anchor in links:
            try:
                text = (await anchor.inner_text()).strip().lower()
                if "pdf" in text or ".pdf" in (await anchor.get_attribute("href") or ""):
                    href = await anchor.get_attribute("href")

                    if not href:
                        continue

                    # Normalize the URL
                    if href.startswith("/"):
                        href = f"https://www.fda.gov{href}"
                    elif not href.startswith("http"):
                        continue  # Skip malformed URLs

                    filename = href.split("/")[-1]
                    safe_name = f"{drug_name.replace(' ', '_')}_{appl_no}_{filename}"

                    save_path = os.path.join("data", "fda_drugs", "pdfs", safe_name)

                    # Skip if already downloaded
                    if os.path.exists(save_path):
                        continue

                    print(f"📄 Downloading PDF: {href}")
                    async with aiohttp.ClientSession() as session:
                        async with session.get(href, timeout=aiohttp.ClientTimeout(total=30)) as response:
                            if response.status == 200:
                                with open(save_path, "wb") as f:
                                    f.write(await response.read())
                            else:
                                print(f"❌ Failed to download {href} | Status: {response.status}")
            except Exception as e:
                print(f"Error extracting/downloading individual PDF: {e}")
    except Exception as e:
        print(f"Error parsing PDF links for {drug_name} ({appl_no}): {e}")


async def scrape_fda():
    all_data = []

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        for letter in LETTERS:
            url = BASE_URL.format(letter)
            print(f"\n--- Visiting letter '{letter}' page: {url}")
            await page.goto(url)

            try:
                await page.wait_for_selector("table", timeout=5000)
            except:
                print(f"No table found for letter {letter}")
                continue

            drug_links = await page.query_selector_all("a[href*='event=overview.process']")
            print(f"Found {len(drug_links)} drugs for letter '{letter}'")

            for index, link in enumerate(drug_links):
                drug_name = await link.inner_text()
                href = await link.get_attribute("href")
                if not href:
                    continue

                appl_no = extract_appl_no_from_href(href)

                overview_url = DOMAIN + href
                print(f"\n>> {letter} {index+1}/{len(drug_links)}: {drug_name} ({appl_no})")

                overview_page = await context.new_page()
                await overview_page.goto(overview_url)

                # Extract tables from overview
                extracted = await extract_all_tables(overview_page, drug_name, appl_no, "Overview", letter)
                all_data.extend(extracted)

                # Download any non-table PDFs
                await extract_and_download_pdfs(overview_page, appl_no, drug_name)

                # Try detail/versions
                try:
                    await overview_page.wait_for_selector("a[href*='event=drugDetails.process']", timeout=3000)
                    detail_links = await overview_page.query_selector_all("a[href*='event=drugDetails.process']")
                    print(f"  Found {len(detail_links)} detail versions")

                    for detail in detail_links:
                        version_name = await detail.inner_text()
                        detail_href = await detail.get_attribute("href")
                        if not detail_href:
                            continue

                        detail_url = DOMAIN + detail_href
                        detail_page = await context.new_page()
                        await detail_page.goto(detail_url)

                        detail_data = await extract_all_tables(detail_page, drug_name, appl_no, version_name, letter)
                        all_data.extend(detail_data)

                        await extract_and_download_pdfs(detail_page, appl_no, drug_name)
                        await detail_page.close()
                except:
                    print(f"  No detail links found for {drug_name}")

                await overview_page.close()

        await browser.close()

    if all_data:
        os.makedirs(SAVE_DIR, exist_ok=True)
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["DrugName", "ApplNo", "Version", "Letter", "TableID"] +
                            [f"Col{i+1}" for i in range(max(len(row) - 5 for row in all_data))])
            writer.writerows(all_data)


if __name__ == "__main__":
    asyncio.run(scrape_fda())
