import httpx, os, json

r = httpx.post(
    "https://api.anthropic.com/v1/messages",
    headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    },
    json={
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 2048,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
        "messages": [{"role": "user", "content": "Search for Executive Chef jobs hiring in Nashville TN. Return a JSON array of results."}]
    },
    timeout=60
)

print("STATUS:", r.status_code)
d = r.json()
if r.status_code != 200:
    print("ERROR:", json.dumps(d, indent=2)[:500])
else:
    for b in d.get("content", []):
        print("BLOCK TYPE:", b["type"])
        if b["type"] == "text":
            print("TEXT:", b["text"][:300])
