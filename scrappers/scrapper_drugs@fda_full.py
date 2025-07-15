import asyncio
from playwright.async_api import async_playwright
import os
import csv

BASE_URL = "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=browseByLetter.page&productLetter={}&ai=0"
DOMAIN = "https://www.accessdata.fda.gov"
LETTERS = [chr(i) for i in range(ord('A'), ord('Z') + 1)]
SAVE_FILE = os.path.join(os.getcwd(), "fda_drugs_all.csv")

async def extract_table_rows(page, drug_name, version_name, letter):
    try:
        await page.wait_for_selector("table#exampleProd", timeout=3000)
        table = await page.query_selector("table#exampleProd")
        rows = await table.query_selector_all("tr")
        data = []
        for row in rows:
            cells = await row.query_selector_all("th, td")
            row_data = [await cell.inner_text() for cell in cells]
            if row_data:
                data.append([drug_name, version_name, letter] + row_data)
        if data:
            print(f"Extracting table for {drug_name} ({version_name})")
            for row in data:
                print("  " + " | ".join(row[3:]))
        return data
    except:
        return []

async def scrape_fda():
    all_data = []

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        for letter in LETTERS:
            url = BASE_URL.format(letter)
            print(f"Visiting: {url}")
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
                print(f"{letter} {index+1}/{len(drug_links)}: {drug_name}")
                href = await link.get_attribute("href")
                if not href:
                    continue
                overview_url = DOMAIN + href

                new_page = await context.new_page()
                await new_page.goto(overview_url)

                extracted = await extract_table_rows(new_page, drug_name, "Overview", letter)
                if extracted:
                    all_data.extend(extracted)
                else:
                    try:
                        await new_page.wait_for_selector("a[href*='event=drugDetails.process']", timeout=3000)
                        detail_links = await new_page.query_selector_all("a[href*='event=drugDetails.process']")
                        print(f"Found {len(detail_links)} detail versions")

                        for detail in detail_links:
                            detail_url = await detail.get_attribute("href")
                            version_name = await detail.inner_text()
                            if not detail_url:
                                continue

                            full_detail_url = DOMAIN + detail_url
                            detail_page = await context.new_page()
                            await detail_page.goto(full_detail_url)

                            extracted_detail = await extract_table_rows(detail_page, drug_name, version_name, letter)
                            if extracted_detail:
                                all_data.extend(extracted_detail)

                            await detail_page.close()
                    except:
                        print(f"No detail links found for {drug_name}")

                await new_page.close()

        await browser.close()

    if all_data:
        with open(SAVE_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["DrugName", "Version", "Letter"] + [f"Col{i+1}" for i in range(max(len(row) - 3 for row in all_data))])
            writer.writerows(all_data)

if __name__ == "__main__":
    asyncio.run(scrape_fda())
