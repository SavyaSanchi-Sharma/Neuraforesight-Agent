import pandas as pd
import requests
import time
import csv
df = pd.read_csv("data/drug_list.csv")

def fetch_studies(drug_name):
    base_url = "https://clinicaltrials.gov/api/v2/studies"
    studies = []
    page_token = None

    while True:
        params = {
            "query.term": drug_name,
            "pageSize": 100,
            "format": "json"
        }
        if page_token:
            params["pageToken"] = page_token

        response = requests.get(base_url, params=params)
        if response.status_code != 200:
            break

        data = response.json()
        studies.extend(data.get("studies", []))
        print(studies)
        page_token = data.get("nextPageToken")
        if not page_token:
            break

        time.sleep(0.5)

    return studies

rows = []

for drug in df["Drug Name"]:
    print(f"getting data for {drug}\n")
    studies = fetch_studies(drug)
    for study in studies:
        info = study.get("protocolSection", {})
        print(info)
        identification = info.get("identificationModule", {})
        print(identification)
        status = info.get("statusModule", {})
        print(status)
        arms = info.get("armsInterventionsModule", {}).get("interventions", [])
        print(arms)
        conditions = info.get("conditionsModule", {}).get("conditions", [])
        print(conditions)
        row = {
            "DrugName": drug,
            "NCTId": identification.get("nctId"),
            "Title": identification.get("officialTitle"),
            "Status": status.get("overallStatus"),
            "StartDate": status.get("startDateStruct", {}).get("date"),
            "Conditions": "; ".join(conditions),
            "InterventionNames": "; ".join([i.get("interventionName") for i in arms if i.get("interventionName")]),
            "InterventionTypes": "; ".join([i.get("interventionType") for i in arms if i.get("interventionType")])
        }
        rows.append(row)

df_out = pd.DataFrame(rows)
df_out.to_csv("data/clinical_trials_data.csv", index=False)
