import asyncio
import pandas as pd
from playwright.async_api import async_playwright
import csv
import os
import json

# Load drug list
drugs_df = pd.read_csv("data/drug_list.csv")
drug_names = drugs_df['Drug Name'].dropna().astype(str).tolist()

# Setup output folders and logging
os.makedirs("output", exist_ok=True)
log_file = open("output/error_log.txt", "w")

def log_error(drug, context, message):
    log_file.write(f"[ERROR] {drug} [{context}]: {message}\n")
    log_file.flush()

async def extract_all_tables(page):
    try:
        tables = await page.query_selector_all("table")
        all_tables = []
        for table in tables:
            table_rows = []
            rows = await table.query_selector_all("tr")
            for row in rows:
                cols = await row.query_selector_all("td")
                if not cols:
                    cols = await row.query_selector_all("th")
                row_data = [await col.inner_text() for col in cols]
                table_rows.append(row_data)
            if table_rows:
                all_tables.append(table_rows)
        print(all_tables)
        return all_tables
    except Exception as e:
        return []

async def extract_text_info(page):
    try:
        data_list = []

        # Expand all dropdowns
        headers = await page.query_selector_all('.ui-accordion-header')
        for header in headers:
            try:
                await header.click()
                await page.wait_for_timeout(200)  # small delay for UI update
            except:
                continue

        # Now extract from all open panels
        panels = await page.query_selector_all('.ui-accordion-content')
        for panel in panels:
            product_data = {}
            html = await panel.inner_html()
            lines = html.split("<br>")
            for line in lines:
                if "<strong>" in line:
                    try:
                        key = line.split("<strong>")[1].split("</strong>")[0].strip().replace(":", "")
                        val_part = line.split("</strong>")[1]
                        val = (
                            val_part.replace("&nbsp;", "")
                            .replace("<br>", "")
                            .strip()
                            .split("<")[0]  # remove trailing tags
                        )
                        if key and val:
                            product_data[key] = val
                    except Exception:
                        continue
            if product_data:
                data_list.append(product_data)
        print(data_list)
        return data_list
    except Exception as e:
        print("text extract error:", e)
        return []

async def fetch_all_data():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        results = []

        for drug in drug_names:
            print(f"Processing: {drug}")
            try:
                await page.goto("https://www.accessdata.fda.gov/scripts/cder/ob/index.cfm")
                await page.fill('input[name="drugname"]', drug)
                await page.click("input#Submit")
                await page.wait_for_load_state('networkidle')
            except Exception as e:
                log_error(drug, "search_page", str(e))
                continue

            # Extract overview table to get Appl_Type and Appl_No
            try:
                overview_tables = await extract_all_tables(page)
                if not overview_tables or len(overview_tables[0]) < 2:
                    log_error(drug, "overview", "No valid overview table found")
                    continue
            except Exception as e:
                log_error(drug, "overview_extract", str(e))
                continue

            headers = overview_tables[0][0]
            for row in overview_tables[0][1:]:
                try:
                    row_data = dict(zip(headers, row))
                    application_number = row_data.get("Appl. No.") or row_data.get("Application Number") or ""
                    appl_type = application_number[0] if application_number else ""
                    appl_no = application_number[1:] if len(application_number) > 1 else ""
                    prod_no = row_data.get("Product No") or "001"
                    table_id = row_data.get("TableID", "").strip()

                    print(appl_no ," ", appl_type)
                    if not appl_no or not appl_type:
                        continue

                    # Product page with anchor (fragment)
                    product_url = f"https://www.accessdata.fda.gov/scripts/cder/ob/results_product.cfm?Appl_Type={appl_type}&Appl_No={appl_no}"
                    if table_id:
                        product_url += f"#{table_id}"


                    if not appl_no or not appl_type:
                        continue

                    # Product page
                    try:
                        await page.goto(product_url)
                        await page.wait_for_load_state('domcontentloaded')
                        product_text = await extract_text_info(page)
                        product_tables = await extract_all_tables(page)
                    except Exception as e:
                        log_error(drug, "product_page", str(e))
                        product_text = {}
                        product_tables = []

                    # Patent page
                    try:
                        patent_url = f"https://www.accessdata.fda.gov/scripts/cder/ob/patent_info.cfm?Product_No={prod_no}&Appl_No={appl_no}&Appl_type={appl_type}"
                        await page.goto(patent_url)
                        await page.wait_for_load_state('domcontentloaded')
                        patent_tables = await extract_all_tables(page)
                    except Exception as e:
                        log_error(drug, "patent_page", str(e))
                        patent_tables = []

                    results.append({
                        "Drug Name": drug,
                        "Appl_No": appl_no,
                        "Appl_Type": appl_type,
                        "Product_No": prod_no,
                        "Product_Text_Info": json.dumps(product_text),  # now a list
                        "Product_Tables": json.dumps(product_tables),
                        "Patent_Tables": json.dumps(patent_tables),
                        "Overview_Tables": json.dumps(overview_tables)
                    })

                except Exception as e:
                    log_error(drug, "row_processing", str(e))
                    continue

        # Save data
        df = pd.DataFrame(results)
        df.to_csv("data/orangebook.csv", index=False)

        await browser.close()
        log_file.close()

if __name__ == '__main__':
    asyncio.run(fetch_all_data())
