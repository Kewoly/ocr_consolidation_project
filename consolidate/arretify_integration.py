# consolidate/arretify_integration.py
from pathlib import Path

try:
    from arretify.pipeline import run_pipeline, load_ocr_file, save_html_file
    from arretify.types import SessionContext
    from arretify.settings import Settings
    ARRETIFY_AVAILABLE = True
except Exception:
    ARRETIFY_AVAILABLE = False

def md_to_arretify_html(md_path: str, out_dir: str):
    md_path = Path(md_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not ARRETIFY_AVAILABLE:
        raise RuntimeError("arretify non installé. pip install git+https://github.com/mte-dgpr/arretify.git")
    ctx = SessionContext(settings=Settings())
    session = run_pipeline(load_ocr_file(ctx, md_path))
    out_html = out_dir / f"{md_path.stem}_arretify.html"
    save_html_file(out_html, session)
    return out_html
