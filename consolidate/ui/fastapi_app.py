# consolidate/ui/fastapi_app.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
import json, datetime
import logging

logger = logging.getLogger("fastapi_app")
app = FastAPI()
BASE = Path("out/consolidated")
STATIC_DIR = BASE.resolve()

# mount consolidated folder as static files (serves HEAD/GET/GET range etc)
if STATIC_DIR.exists():
    app.mount("/consolidated", StaticFiles(directory=str(STATIC_DIR)), name="consolidated")
else:
    logger.warning(f"Static consolidated dir not found: {STATIC_DIR}")

# Serve the UI index.html (ensure this file exists)
INDEX = Path(__file__).parent.joinpath("static", "index.html")
if not INDEX.exists():
    logger.warning(f"UI index not found at {INDEX}")

@app.get("/")
async def index():
    if INDEX.exists():
        return FileResponse(INDEX)
    return JSONResponse({"error": "index.html missing"}, status_code=500)

@app.get("/ops")
def list_ops():
    p = BASE / "applied_ops.json"
    if not p.exists():
        # fallback: try out/work/applied_ops.json or top-level applied_ops.json
        alt = Path("out").joinpath("applied_ops.json")
        if alt.exists():
            p = alt
        else:
            raise HTTPException(404, "applied_ops.json introuvable (search in out/consolidated and out/)")
    try:
        return JSONResponse(json.loads(p.read_text(encoding="utf-8")))
    except Exception as e:
        raise HTTPException(500, f"Impossible de lire applied_ops.json: {e}")

@app.post("/ops/{op_id}/decision")
def op_decision(op_id: str, request: Request):
    body = request.json() if hasattr(request, "json") else None
    # simple storing of decisions in out/consolidated/decision_log.json
    logp = BASE / "decision_log.json"
    log = json.loads(logp.read_text(encoding="utf-8")) if logp.exists() else []
    entry = {"op_id": op_id, "decision": body, "ts": datetime.datetime.utcnow().isoformat() + "Z"}
    log.append(entry)
    logp.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "entry": entry}
