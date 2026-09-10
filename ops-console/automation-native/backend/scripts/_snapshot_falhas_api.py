# -*- coding: utf-8 -*-
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client

User = get_user_model()
user = User.objects.filter(username="gerente.dev").first() or User.objects.filter(is_superuser=True).first()
c = Client()
c.force_login(user)

start = date.today().replace(day=1).isoformat()
end = date.today().isoformat()
q = f"start_date={start}&end_date={end}&oficial=true&localidade=Geral"

paths = {
    "kpis": f"/api/v1/falhas/kpis/?{q}",
    "reincidence_top3": f"/api/v1/falhas/reincidence/?{q}",
    "ult3m_meta": f"/api/v1/falhas/ult3m/?{q}",
    "clientes": f"/api/v1/falhas/clientes-workflows/?{q}",
    "rank_delta": f"/api/v1/falhas/rank-delta/?{q}",
    "matrix": f"/api/v1/falhas/matrix/?{q}",
    "sync_imports": "/api/v1/falhas/sync/imports/",
    "filters": "/api/v1/falhas/filters/",
}

out = {"user": user.username if user else None, "query": q}
for name, path in paths.items():
    r = c.get(path, HTTP_HOST="localhost")
    data = r.json() if r.status_code == 200 else {"status": r.status_code, "body": r.content[:200].decode("utf-8", errors="replace")}
    if name == "reincidence_top3" and isinstance(data, dict):
        rows = data.get("rows") or data.get("agentes") or []
        data = {**{k: v for k, v in data.items() if k not in ("rows", "agentes")}, "rows_sample": rows[:3]}
    if name == "ult3m_meta" and isinstance(data, dict):
        ag = data.get("agentes") or []
        data = {k: v for k, v in data.items() if k != "agentes"}
        data["agentes_sample_count"] = len(ag)
        data["agentes_sample"] = ag[:2]
    if name == "clientes" and isinstance(data, dict):
        data["top_clientes_sample"] = (data.get("top_clientes") or data.get("clientes") or [])[:3]
        for k in ("clientes", "workflows", "top_clientes", "top_workflows"):
            if k in data and isinstance(data[k], list) and len(data[k]) > 5:
                data[k] = data[k][:5]
    if name == "matrix" and isinstance(data, dict):
        for k in list(data.keys()):
            if isinstance(data.get(k), list) and len(data[k]) > 2:
                data[k] = data[k][:2]
    out[name] = data

out_path = "scripts/_api_snapshot_out.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
print("written", out_path)
