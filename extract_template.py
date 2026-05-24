import django, os, re, json, requests
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vesta_rental_index.settings")
django.setup()

KEY = os.environ["MAILERLITE_API_KEY"]
HEADERS = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json", "Accept": "application/json"}
BASE = "https://connect.mailerlite.com/api"

for cid in ["178295163828831519", "178293680546776853", "146247627011261507"]:
    r = requests.get(BASE + "/campaigns/" + cid, headers=HEADERS)
    data = r.json().get("data", {})
    emails = data.get("emails", [])
    name = data.get("name", "")
    print("=== Campaign:", name, "===")
    if emails:
        content = emails[0].get("content", "")
        imgs = re.findall(r'src="(https?://[^"]+)"', content)
        print("Images:", imgs[:10])
        bgs = re.findall(r"background(?:-color)?:\s*([#\w(),.%\s]+?)(?:;|\")", content)
        print("BG colors:", list(set(bgs))[:15])
        # Print first 3000 chars of content
        print("HTML preview:", content[:3000])
        print()
