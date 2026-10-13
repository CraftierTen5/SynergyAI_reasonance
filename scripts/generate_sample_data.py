import csv
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from faker import Faker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.csv_storage import CSVStorage, FIELDS

SEED = 20261013
CHANNELS = ["Web portal", "Phone helpline", "Social media", "In-person municipal office"]
EXAMPLES = {
    "water_supply": [("en", "Water supply has been absent since yesterday evening in {place}."), ("hi", "{place} में कल शाम से पानी नहीं आ रहा है।"), ("hinglish", "{place} mein kal shaam se paani nahi aa raha hai.")],
    "leakage": [("en", "A water pipe is leaking near {place}."), ("hi", "{place} के पास पानी की पाइप में रिसाव है।"), ("hinglish", "{place} ke paas paani ki pipe leak ho rahi hai.")],
    "sanitation": [("en", "Please clean the public area near {place}; it is unsanitary."), ("hi", "{place} के पास सार्वजनिक जगह की सफाई नहीं हुई है।"), ("hinglish", "{place} ke paas safai nahi hui hai.")],
    "garbage": [("en", "Garbage has not been collected from {place} for several days."), ("hi", "{place} से कई दिनों से कचरा नहीं उठाया गया।"), ("hinglish", "{place} se kai din se kachra nahi uthaya gaya.")],
    "drainage": [("en", "The drain near {place} is blocked and overflowing."), ("hi", "{place} के पास नाली बंद है और पानी बह रहा है।"), ("hinglish", "{place} ke paas naali block hai aur overflow ho rahi hai.")],
    "roads": [("en", "The road surface is damaged near {place}."), ("hi", "{place} के पास सड़क की सतह खराब है।"), ("hinglish", "{place} ke paas road kharab hai.")],
    "potholes": [("en", "There is a large pothole near {place}."), ("hi", "{place} के पास बड़ा गड्ढा है।"), ("hinglish", "{place} ke paas bada pothole hai.")],
    "streetlights": [("en", "The streetlight by {place} is not working."), ("hi", "{place} के पास की स्ट्रीट लाइट काम नहीं कर रही।"), ("hinglish", "{place} ke paas streetlight kaam nahi kar rahi.")],
    "electrical_hazard": [("en", "A loose electrical wire is visible near {place}."), ("hi", "{place} के पास बिजली का तार ढीला दिखाई दे रहा है।"), ("hinglish", "{place} ke paas electric wire latak rahi hai.")],
    "stray_animals": [("en", "Stray animals are gathering near {place}."), ("hi", "{place} के पास आवारा जानवर इकट्ठा हो रहे हैं।"), ("hinglish", "{place} ke paas stray animals jama ho rahe hain.")],
    "public_infrastructure": [("en", "The public bench near {place} is damaged."), ("hi", "{place} के पास सार्वजनिक बेंच टूटी हुई है।"), ("hinglish", "{place} ke paas public bench toot gayi hai.")],
    "unclear": [("en", "Please help with this issue. I am not sure which service it concerns."), ("hi", "कृपया इस समस्या में मदद करें, मुझे विभाग पता नहीं है।"), ("hinglish", "Please is issue mein help karein, details clear nahi hain.")],
}


def generate_records(count=500, seed=SEED):
    random.seed(seed)
    fake = Faker("en_IN")
    Faker.seed(seed)
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    records = []
    for _ in range(count):
        lang, template = random.choice(random.choice(list(EXAMPLES.values())))
        place = random.choice([f"fictional {fake.word()} block", f"fictional sector {random.randint(1, 40)}", f"fictional ward {random.randint(1, 80)}"])
        submitted = base + timedelta(minutes=random.randint(0, 365 * 24 * 60))
        ward = random.choice([None, f"W-{random.randint(1, 80):02d}"])
        has_location = random.random() > 0.16
        text = template.format(place=place)
        created = submitted + timedelta(seconds=random.randint(0, 120))
        records.append({"complaint_id": str(UUID(int=random.getrandbits(128))), "source_channel": random.choice(CHANNELS), "original_text": text, "language_code": lang,
                        "location_text": place if has_location else "", "ward_id": ward or "", "latitude": "" if not has_location or random.random() > .45 else f"{random.uniform(20.0, 29.0):.6f}",
                        "longitude": "" if not has_location or random.random() > .45 else f"{random.uniform(72.0, 88.0):.6f}", "submitted_at": submitted.isoformat(),
                        "processing_status": "RECEIVED", "idempotency_key": "", "attachment_refs": json.dumps([f"data/uploads/sample-{random.randint(1, 25)}.jpg"] if random.random() < .12 else []),
                        "created_at": created.isoformat(), "updated_at": created.isoformat()})
    return records


def main():
    records = generate_records()
    DATA = ROOT / "data"
    DATA.mkdir(exist_ok=True)
    with (DATA / "sample_complaints.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(records)
    with (DATA / "sample_complaints.json").open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.write("\n")
    (DATA / "uploads").mkdir(exist_ok=True)
    print(f"Generated {len(records)} records in {DATA}")


if __name__ == "__main__":
    main()
