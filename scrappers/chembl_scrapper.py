import asyncio
import aiohttp
import aiofiles
import pandas as pd
import json
import os
import re

BASE = "https://www.ebi.ac.uk/chembl/api/data"
CONCURRENCY = 10
ACTIVITY_LIMIT = 1000

async def fetch(session, url, params=None):
    headers = {"Accept": "application/json"}
    async with session.get(url, params=params, headers=headers) as r:
        if r.status == 404:
            return None
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if "json" not in ct:
            txt = await r.text()
            return txt
        return await r.json()

async def fetch_image_svg(session, chembl_id):
    headers = {"Accept": "image/svg+xml"}
    url = f"{BASE}/molecule/{chembl_id}.svg"
    async with session.get(url, headers=headers) as r:
        if r.status == 404:
            return ""
        r.raise_for_status()
        return await r.text()

def normalize_name(s):
    if not isinstance(s, str):
        return ""
    return re.sub(r"[\s\-_,.;:/]+", "", s).lower()

async def get_chembl_id(session, drug_name):
    data = await fetch(session, f"{BASE}/molecule/search", {"q": drug_name})
    if not data:
        return None
    if isinstance(data, str):
        return None
    molecules = data.get("molecules", [])
    if not molecules:
        return None
    target_norm = normalize_name(drug_name)
    best = None
    best_score = -1
    for m in molecules:
        cid = m.get("molecule_chembl_id")
        pref = m.get("pref_name") or ""
        score = 0
        if normalize_name(pref) == target_norm:
            score += 100
        syns = []
        syn_field = m.get("molecule_synonyms")
        if isinstance(syn_field, list):
            syns = [x.get("molecule_synonym") for x in syn_field if isinstance(x, dict)]
        for syn in syns:
            if normalize_name(syn) == target_norm:
                score += 80
        s = m.get("score")
        if isinstance(s, (int, float)):
            score += int(s)
        if score > best_score:
            best_score = score
            best = cid
    if best:
        return best
    return molecules[0].get("molecule_chembl_id")

async def process_drug(session, drug_name, sem):
    print(drug_name)
    async with sem:
        cid = await get_chembl_id(session, drug_name)
        if not cid:
            return {"drug_name": drug_name, "chembl_id": None, "error": "not_found"}
        molecule = await fetch(session, f"{BASE}/molecule/{cid}.json")
        drug = await fetch(session, f"{BASE}/drug/{cid}.json")
        mech = await fetch(session, f"{BASE}/mechanism", {"molecule_chembl_id": cid})
        act = await fetch(session, f"{BASE}/activity", {"molecule_chembl_id": cid, "limit": ACTIVITY_LIMIT})
        svg = await fetch_image_svg(session, cid)
        mechanisms = []
        if isinstance(mech, dict):
            mechanisms = mech.get("mechanisms", [])
        activities = []
        if isinstance(act, dict):
            activities = act.get("activities", [])
            print(cid)
            print(molecule)
            print(drug)
            print(activities)
            print(mechanisms)
        return {
            "drug_name": drug_name,
            "chembl_id": cid,
            "molecule": molecule if isinstance(molecule, dict) else {},
            "drug": drug if isinstance(drug, dict) else {},
            "mechanism": mechanisms,
            "activities": activities,
            "structure_svg": svg
        }

def flatten_record(rec):
    mol = rec.get("molecule") or {}
    props = mol.get("molecule_properties") or {}
    structs = mol.get("molecule_structures") or {}
    return {
        "drug_name": rec.get("drug_name"),
        "chembl_id": rec.get("chembl_id"),
        "pref_name": mol.get("pref_name"),
        "max_phase": mol.get("max_phase"),
        "first_approval": mol.get("first_approval"),
        "molecule_type": mol.get("molecule_type"),
        "therapeutic_flag": mol.get("therapeutic_flag"),
        "oral": mol.get("oral"),
        "parenteral": mol.get("parenteral"),
        "topical": mol.get("topical"),
        "molecule_form": mol.get("structure_type"),
        "full_mwt": props.get("full_mwt"),
        "alogp": props.get("alogp"),
        "cx_logp": props.get("cx_logp"),
        "hba": props.get("hba"),
        "hbd": props.get("hbd"),
        "psa": props.get("psa"),
        "rtb": props.get("rtb"),
        "full_molformula": props.get("full_molformula"),
        "canonical_smiles": structs.get("canonical_smiles"),
        "standard_inchi": structs.get("standard_inchi"),
        "standard_inchi_key": structs.get("standard_inchi_key"),
        "activities_count": len(rec.get("activities") or []),
        "mechanisms_count": len(rec.get("mechanism") or []),
        "has_drug_record": bool(rec.get("drug"))
    }

async def main():
    df = pd.read_csv("data/drug_list.csv")
    drugs = df["Drug Name"].dropna().astype(str).unique().tolist()
    os.makedirs("structures", exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    results = []
    flattened = []
    async with aiohttp.ClientSession() as session:
        tasks = [process_drug(session, name, sem) for name in drugs]
        for fut in asyncio.as_completed(tasks):
            rec = await fut
            results.append(rec)
            cid = rec.get("chembl_id")
            svg = rec.get("structure_svg") or ""
            if cid and svg:
                async with aiofiles.open(f"structures/{cid}.svg", "w") as f:
                    await f.write(svg)
            flattened.append(flatten_record(rec))
    async with aiofiles.open("chembl_results.json", "w") as f:
        await f.write(json.dumps(results, indent=2))
    pd.DataFrame(flattened).to_csv("chembl.csv", index=False)

if __name__ == "__main__":
    asyncio.run(main())
