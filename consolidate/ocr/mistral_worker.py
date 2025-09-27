# consolidate/ocr/mistral_worker.py
from pathlib import Path
import os
import logging
import json
from dotenv import load_dotenv
import datauri

load_dotenv()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_OCR_MODEL", "mistral-ocr-latest")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Arrêtify integration (optionnelle)
try:
    from arretify.pipeline import run_pipeline, load_ocr_file, save_html_file
    from arretify.types import SessionContext
    from arretify.settings import Settings
    ARRETIFY_AVAILABLE = True
except Exception:
    ARRETIFY_AVAILABLE = False

# Mistral SDK
try:
    from mistralai import Mistral
    MISTRAL_SDK_AVAILABLE = True
except Exception:
    MISTRAL_SDK_AVAILABLE = False


def _make_client():
    if not MISTRAL_API_KEY:
        raise RuntimeError("MISTRAL_API_KEY non défini dans l'environnement (.env)")
    if not MISTRAL_SDK_AVAILABLE:
        raise RuntimeError("SDK 'mistralai' non installé. pip install mistralai")
    return Mistral(api_key=MISTRAL_API_KEY)


def _save_image_from_datauri(image_obj, out_images_dir: Path):
    """Enregistre une image encodée en datauri renvoyée par Mistral."""
    b64 = getattr(image_obj, "image_base64", None) or image_obj.get("image_base64", None)
    img_id = getattr(image_obj, "id", None) or image_obj.get("id", None) or "image"
    if not b64:
        logger.debug("Image sans base64 trouvée, on skip")
        return None
    parsed = datauri.parse(b64)
    out_images_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_images_dir / f"{img_id}"
    out_path.write_bytes(parsed.data)
    return out_path


def _safe_get(obj, name, default=None):
    """
    Retourne la valeur pour 'name' en supportant :
      - obj.name (obj a un attribut)
      - obj.get(name) (obj est un dict-like)
    """
    if obj is None:
        return default
    # Prefer attribute access for pydantic objects
    try:
        val = getattr(obj, name)
        # getattr may raise AttributeError; if it returns None or empty list/string we still accept it
        return val if val is not None else (obj.get(name) if hasattr(obj, "get") else default)
    except Exception:
        # fallback to mapping access when obj supports it
        if hasattr(obj, "get"):
            try:
                return obj.get(name, default)
            except Exception:
                return default
        return default


def _create_markdown_and_save(ocr_response, out_md_path: Path, images_dir: Path):
    """
    Parcourt ocr_response.pages, concatène page.markdown et sauve images.
    Supporte à la fois les objets pydantic (avec attributs) et les dicts.
    """
    md_lines = []
    pages = _safe_get(ocr_response, "pages", None)
    # si pages est un seul objet/itérable non-list, essayer d'en faire une liste
    if pages is None:
        # fallback : ocr_response peut contenir 'markdown' global
        md = _safe_get(ocr_response, "markdown", None)
        if md:
            out_md_path.write_text(md, encoding="utf-8")
            return out_md_path
        # dernier recours : stringify la réponse
        out_md_path.write_text(json.dumps(ocr_response, default=str, ensure_ascii=False), encoding="utf-8")
        return out_md_path

    # si pages est un objet unique pas itérable (rare), on tente de l'encapsuler
    try:
        iter(pages)
    except TypeError:
        pages = [pages]

    for page in pages:
        md = _safe_get(page, "markdown", None) or ""
        md_lines.append(md)

        images = _safe_get(page, "images", None) or []
        for image in images:
            try:
                _save_image_from_datauri(image, images_dir)
            except Exception as e:
                logger.warning(f"Impossible de sauver une image: {e}")

    out_md_path.write_text("\n\n".join(md_lines), encoding="utf-8")
    return out_md_path

def process_one_pdf_with_mistral(pdf_path: str, out_dir: str, model: str = None):
    """
    Upload -> OCR -> save .md + images -> (optionnel) Arrêtify -> return path to html or md
    """
    model = model or MISTRAL_MODEL
    client = _make_client()
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = out_dir / "images"
    images_dir.mkdir(exist_ok=True)

    # 1) upload file
    logger.info("Upload du PDF vers Mistral...")
    uploaded = client.files.upload(
        file={
            "file_name": pdf_path.name,
            "content": open(pdf_path, "rb"),
        },
        purpose="ocr",
    )
    # récupérer id
    file_id = getattr(uploaded, "id", None) or uploaded.get("id", None) or (uploaded.get("file") or {}).get("id")
    logger.info(f"Upload ok, file_id={file_id}")

    # 2) get signed url (si disponible) - pratique pour la suite
    signed_url = None
    try:
        signed = client.files.get_signed_url(file_id=file_id)
        signed_url = getattr(signed, "url", None) or signed.get("url", None)
    except Exception:
        signed_url = None

    # 3) lancer OCR
    logger.info("Demande de traitement OCR...")
    document_arg = {"type": "document_url", "document_url": signed_url} if signed_url else {"type": "file_id", "file_id": file_id}
    ocr_response = client.ocr.process(
        model=model,
        document=document_arg,
        include_image_base64=True,
    )

    # 4) sauvegarder markdown et images
    md_path = out_dir / f"{pdf_path.stem}.md"
    _create_markdown_and_save(ocr_response, md_path, images_dir)
    logger.info(f"Markdown sauvegardé: {md_path}")

    # 5) si Arrêtify dispo -> produire html sémantique
    if ARRETIFY_AVAILABLE:
        try:
            ctx = SessionContext(settings=Settings())
            session = run_pipeline(load_ocr_file(ctx, md_path))
            out_html = out_dir / f"{pdf_path.stem}_arretify.html"
            save_html_file(out_html, session)
            logger.info(f"HTML Arrêtify produit: {out_html}")
            return out_html
        except Exception as e:
            logger.warning(f"Arrêtify a échoué: {e} — retourne le .md")
            return md_path

    return md_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", required=True, help="PDF à traiter")
    parser.add_argument("-o", "--output", required=True, help="Dossier de sortie")
    parser.add_argument("--model", default=None, help="Modèle OCR Mistral optionnel")
    args = parser.parse_args()
    res = process_one_pdf_with_mistral(args.input, args.output, model=args.model)
    print("Résultat:", res)
