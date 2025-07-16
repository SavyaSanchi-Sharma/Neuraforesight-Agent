import pandas as pd
import asyncio
import aiohttp
import aiofiles
import json
import os

BASE = "https://www.ebi.ac.uk/chembl/api/data"

async def fetch(session, url, params=None):
    headers={
        "Accept":"application/json"
    }
    async with session.get(url, params=params,headers=headers) as r:
        if r.status==404:
            return None
        r.raise_for_status()
        return await r.json()

async def fetch_image(session, chembl_id):
    url = f"{BASE}/molecule/{chembl_id}.svg"
    async with session.get(url) as r:
        if r.status==404:
            return None
        r.raise_for_status()
        return await r.text()

async def get_chembl_id(session, drug_name):
    url = f"{BASE}/molecule/search"
    data = await fetch(session, url, {"q": drug_name})
    matches = data.get("molecules", [])
    print(matches)
    if matches:
        return matches[0].get("molecule_chembl_id")
    return None

async def process_drug(session, drug_name):
    chembl_id = await get_chembl_id(session, drug_name)
    if not chembl_id:
        return {"drug_name": drug_name, "error": "ChEMBL ID not found"}

    out = {"drug_name": drug_name, "chembl_id": chembl_id}
    out["molecule"] = await fetch(session, f"{BASE}/molecule/{chembl_id}.json")
    
    drug_info = await fetch(session, f"{BASE}/drug/{chembl_id}.json")
    out["drug"] = drug_info if drug_info else {}

    mech_info = await fetch(session, f"{BASE}/mechanism", {"molecule_chembl_id": chembl_id})
    out["mechanism"] = mech_info.get("mechanisms", []) if mech_info else []

    act_info = await fetch(session, f"{BASE}/activity", {"molecule_chembl_id": chembl_id, "limit": 1000})
    out["activities"] = act_info.get("activities", []) if act_info else []

    out["structure_svg"] = await fetch_image(session, chembl_id)
    return out

async def main():
    df = pd.read_excel("data/100Drugs.xlsx")
    drugs = df["Drug Name"].dropna().unique().tolist()

    results = []
    os.makedirs("data/structures", exist_ok=True)

    async with aiohttp.ClientSession() as session:
        tasks = [process_drug(session, name) for name in drugs]
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)

            cid = result.get("chembl_id")
            if cid and result.get("structure_svg"):
                async with aiofiles.open(f"data/structures/{cid}.svg", "w") as f:
                    await f.write(result["structure_svg"])

    async with aiofiles.open("data/chembl_results.json", "w") as f:
        await f.write(json.dumps(results, indent=2))

asyncio.run(main())
