"""Interface Google Drive : pull Inbox, push Outbox/Archive/Quarantine.

Utilise un compte de service Google (Service Account JSON) pour acceder aux
dossiers Drive partages avec son email.
"""
from __future__ import annotations

import io
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False

from .config import (
    GOOGLE_SERVICE_ACCOUNT_JSON,
    DRIVE_FOLDER_INBOX,
    DRIVE_FOLDER_OUTBOX,
    DRIVE_FOLDER_ARCHIVE,
    DRIVE_FOLDER_QUARANTINE,
)


SCOPES = ["https://www.googleapis.com/auth/drive"]


@dataclass
class DriveFile:
    """Representation simplifiee d'un fichier Drive."""
    id: str
    name: str
    mime_type: str
    size: Optional[int] = None
    parent_id: Optional[str] = None


# ============================================================================
# Service Drive
# ============================================================================
def _build_service():
    """Construit le client Drive a partir du Service Account JSON."""
    if not GOOGLE_AVAILABLE:
        raise RuntimeError(
            "google-api-python-client non installe. Lancer: pip install google-api-python-client google-auth"
        )
    if not GOOGLE_SERVICE_ACCOUNT_JSON.exists():
        raise FileNotFoundError(
            f"Service account JSON introuvable: {GOOGLE_SERVICE_ACCOUNT_JSON}. "
            "Verifier la variable GOOGLE_SERVICE_ACCOUNT_JSON dans .env."
        )
    creds = service_account.Credentials.from_service_account_file(
        str(GOOGLE_SERVICE_ACCOUNT_JSON), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ============================================================================
# Operations
# ============================================================================
def list_pdfs_in_folder(folder_id: str) -> list[DriveFile]:
    """Liste les PDF dans un dossier Drive."""
    svc = _build_service()
    query = (
        f"'{folder_id}' in parents "
        f"and mimeType='application/pdf' "
        f"and trashed = false"
    )
    files: list[DriveFile] = []
    page_token: str | None = None
    while True:
        resp = svc.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, size, parents)",
            pageToken=page_token,
            pageSize=100,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        for f in resp.get("files", []):
            files.append(DriveFile(
                id=f["id"], name=f["name"], mime_type=f["mimeType"],
                size=int(f["size"]) if "size" in f else None,
                parent_id=f["parents"][0] if f.get("parents") else None,
            ))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(file_id: str, dest_path: Path) -> Path:
    """Telecharge un fichier Drive vers un chemin local."""
    svc = _build_service()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    request = svc.files().get_media(fileId=file_id)
    with dest_path.open("wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
    logger.info(f"Drive download: {dest_path}")
    return dest_path


def upload_file(local_path: Path, folder_id: str, name: str | None = None) -> str:
    """Uploade un fichier local vers un dossier Drive. Retourne le file_id."""
    svc = _build_service()
    name = name or local_path.name
    mime_type, _ = mimetypes.guess_type(local_path.name)
    mime_type = mime_type or "application/octet-stream"

    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    body = {"name": name, "parents": [folder_id]}
    f = svc.files().create(
        body=body, media_body=media, fields="id, name",
        supportsAllDrives=True,
    ).execute()
    logger.info(f"Drive upload: {name} -> {f['id']} (folder={folder_id})")
    return f["id"]


def move_file(file_id: str, new_parent_id: str) -> None:
    """Deplace un fichier Drive d'un dossier a un autre."""
    svc = _build_service()
    # Recupere les parents actuels
    f = svc.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
    old_parents = ",".join(f.get("parents", []))
    svc.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=old_parents,
        fields="id, parents",
        supportsAllDrives=True,
    ).execute()
    logger.info(f"Drive move: {file_id} -> folder {new_parent_id}")


def delete_file(file_id: str) -> None:
    """Supprime un fichier Drive (corbeille)."""
    svc = _build_service()
    svc.files().delete(fileId=file_id, supportsAllDrives=True).execute()
    logger.info(f"Drive delete: {file_id}")


# ============================================================================
# Helpers haut niveau utilises par le pipeline
# ============================================================================
def pull_inbox_to_local(local_dir: Path, max_files: int = 10) -> list[tuple[DriveFile, Path]]:
    """Telecharge jusqu'a N PDFs de l'inbox Drive vers un dossier local.

    Retourne la liste de (DriveFile, chemin_local) pour suivi.
    """
    if not DRIVE_FOLDER_INBOX:
        raise RuntimeError("DRIVE_FOLDER_INBOX non defini dans .env")

    files = list_pdfs_in_folder(DRIVE_FOLDER_INBOX)[:max_files]
    local_dir.mkdir(parents=True, exist_ok=True)
    result: list[tuple[DriveFile, Path]] = []
    for f in files:
        local_path = local_dir / f.name
        if local_path.exists():
            logger.info(f"Deja telecharge, skip: {f.name}")
            continue
        download_file(f.id, local_path)
        result.append((f, local_path))
    return result


def push_outbox(json_path: Path, name: str | None = None) -> str:
    """Pousse un JSON resultat vers Drive Outbox."""
    if not DRIVE_FOLDER_OUTBOX:
        raise RuntimeError("DRIVE_FOLDER_OUTBOX non defini dans .env")
    return upload_file(json_path, DRIVE_FOLDER_OUTBOX, name)


def archive_pdf(drive_file_id: str) -> None:
    """Deplace une facture traitee de Inbox vers Archive."""
    if not DRIVE_FOLDER_ARCHIVE:
        raise RuntimeError("DRIVE_FOLDER_ARCHIVE non defini dans .env")
    move_file(drive_file_id, DRIVE_FOLDER_ARCHIVE)


def quarantine_pdf(drive_file_id: str) -> None:
    """Deplace une facture problematique de Inbox vers Quarantine."""
    if not DRIVE_FOLDER_QUARANTINE:
        raise RuntimeError("DRIVE_FOLDER_QUARANTINE non defini dans .env")
    move_file(drive_file_id, DRIVE_FOLDER_QUARANTINE)
