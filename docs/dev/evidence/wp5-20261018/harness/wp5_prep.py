import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8080"
sys.path.insert(0, r"D:\LLM\self_programming\aistudio-api\src")

mode = sys.argv[1]

if mode == "active":
    from aistudio_api.infrastructure.account.account_store import AccountStore

    store = AccountStore()
    acc_id = sys.argv[2]
    store.set_active_account(acc_id)
    acc = store.get_active_account()
    tier = getattr(acc, "tier", None) if acc else None
    print(f"active set -> {acc.id if acc else None} ({acc.name if acc else ''}) tier={tier}")

elif mode == "refresh":
    client = httpx.Client(trust_env=False, timeout=300)
    for _attempt in range(3):
        r = client.post(f"{BASE}/accounts/refresh", json={"all": True})
        if r.status_code == 409:
            print("refresh busy (409), retry in 5s...")
            time.sleep(5)
            continue
        print(f"refresh status: {r.status_code}")
        if r.status_code == 200:
            print(json.dumps(r.json(), ensure_ascii=False)[:800])
        else:
            print(r.text[:400])
        break
    from aistudio_api.infrastructure.account.account_store import AccountStore

    store = AccountStore()
    for acc in store.list_accounts():
        print(
            f"meta: {acc.id} name={acc.name} email={acc.email} "
            f"tier={acc.tier} expired={acc.expired}"
        )
else:
    raise SystemExit("mode: active|refresh")