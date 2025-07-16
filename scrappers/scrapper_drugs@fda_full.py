import asyncio
from playwright.async_api import async_playwright
import os
import csv

DOMAIN = "https://www.accessdata.fda.gov"
BASE_URL = f"{DOMAIN}/scripts/cder/daf/index.cfm?event=browseByLetter.page&productLetter={{}}&ai=0"
LETTERS = [chr(i) for i in range(ord('A'), ord('Z') + 1)]

SAVE_DIR = os.path.join(os.getcwd(), "data/fda_downloads")
PDF_DIR = os.path.join(SAVE_DIR, "pdfs")
CSV_FILE = os.path.join(SAVE_DIR, "fda_all_tables.csv")

os.makedirs(PDF_DIR, exist_ok=True)

async def extract_all_tables(page, drug_name, version, letter):
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
                        await download_pdf(page, full_url)
                        row_data.append(f"{text} ({full_url})")
                    else:
                        row_data.append(text)
                else:
                    row_data.append(await cell.inner_text())
            if row_data:
                all_data.append([drug_name, version, letter, f"Table{i+1}"] + row_data)
    print(all_data)

    return all_data

async def download_pdf(page, url):
    filename = os.path.basename(url.split("#")[0])
    save_path = os.path.join(PDF_DIR, filename)

    if os.path.exists(save_path):
        return  # skip if already downloaded

    try:
        async with page.expect_download() as download_info:
            # Simulate clicking a download using JS if needed
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

async def extract_and_download_pdfs(page):
    anchors = await page.query_selector_all("a[href$='.pdf']")
    for anchor in anchors:
        href = await anchor.get_attribute("href")
        if href:
            full_url = href if href.startswith("http") else DOMAIN + href
            await download_pdf(page, full_url)

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

                overview_url = DOMAIN + href
                print(f"\n>> {letter} {index+1}/{len(drug_links)}: {drug_name}")
                overview_page = await context.new_page()
                await overview_page.goto(overview_url)

                # Extract tables from overview
                extracted = await extract_all_tables(overview_page, drug_name, "Overview", letter)
                all_data.extend(extracted)

                # Download any non-table PDFs
                await extract_and_download_pdfs(overview_page)

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

                        detail_data = await extract_all_tables(detail_page, drug_name, version_name, letter)
                        all_data.extend(detail_data)

                        await extract_and_download_pdfs(detail_page)
                        await detail_page.close()
                except:
                    print(f"  No detail links found for {drug_name}")

                await overview_page.close()

        await browser.close()

    if all_data:
        os.makedirs(SAVE_DIR, exist_ok=True)
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["DrugName", "Version", "Letter", "TableID"] +
                            [f"Col{i+1}" for i in range(max(len(row) - 4 for row in all_data))])
            writer.writerows(all_data)

if __name__ == "__main__":
    asyncio.run(scrape_fda())
