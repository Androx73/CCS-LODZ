"""
HomeVault - first version (Lab 8 core).

What this file does, in plain words:
- Starts a small web server (FastAPI).
- Connects to Azure Blob Storage using YOUR Azure login (no passwords or keys in the code).
- Offers 4 operations over HTTP(S): list files, upload a file, download a file, list versions of a file.
- Serves the web page in the "static" folder (the screen you see in the browser).

Login, permissions (read/write/update) and the LogBook come in the next steps.
"""

import os
from urllib.parse import quote

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

# 1. Read settings from the .env file (the storage account name, never secrets).
load_dotenv()
ACCOUNT_NAME = os.environ["STORAGE_ACCOUNT_NAME"]
CONTAINER_NAME = os.getenv("STORAGE_CONTAINER", "files")

# 2. Rules for files (from the project document).
MAX_SIZE_MB = 50
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".txt", ".jpg", ".jpeg", ".png", ".webp"}

# 3. Connect to Azure Blob Storage.
#    DefaultAzureCredential uses "az login" on your Mac, and Managed Identity once deployed to Azure.
credential = DefaultAzureCredential()
service = BlobServiceClient(
    account_url=f"https://{ACCOUNT_NAME}.blob.core.windows.net",
    credential=credential,
)
container = service.get_container_client(CONTAINER_NAME)

app = FastAPI(title="HomeVault API", version="0.1")


@app.get("/api/files")
def list_files():
    """Return the list of files: name, size and last change date."""
    return [
        {
            "name": blob.name,
            "size_bytes": blob.size,
            "last_modified": blob.last_modified.isoformat(),
        }
        for blob in container.list_blobs()
    ]


@app.post("/api/files")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file. If a file with the same name exists, Azure keeps the old one as a previous version."""
    name = os.path.basename(file.filename or "")
    extension = os.path.splitext(name)[1].lower()

    if not name:
        raise HTTPException(status_code=400, detail="File name is missing.")
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {extension or '(none)'} is not allowed.")

    data = await file.read()
    if len(data) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File is larger than {MAX_SIZE_MB} MB.")

    container.upload_blob(
        name=name,
        data=data,
        overwrite=True,  # with blob versioning enabled, the previous content is kept as a version
        content_settings=ContentSettings(content_type=file.content_type or "application/octet-stream"),
    )
    return {"message": "Upload successful", "name": name, "size_bytes": len(data)}


@app.get("/api/files/{name}/download")
def download_file(name: str, version_id: str | None = None):
    """Download the current version of a file, or an older one if version_id is given."""
    blob = container.get_blob_client(name)
    try:
        stream = blob.download_blob(version_id=version_id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="File not found.")

    content_type = stream.properties.content_settings.content_type or "application/octet-stream"
    return StreamingResponse(
        stream.chunks(),
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@app.get("/api/files/{name}/versions")
def list_versions(name: str):
    """Return every version of one file: number, date, size and Azure version id."""
    versions = [
        blob
        for blob in container.list_blobs(name_starts_with=name, include=["versions"])
        if blob.name == name
    ]
    if not versions:
        raise HTTPException(status_code=404, detail="File not found.")

    versions.sort(key=lambda b: b.last_modified)  # oldest first -> version 1, 2, 3...
    return [
        {
            "version": number,
            "date": blob.last_modified.isoformat(),
            "size_bytes": blob.size,
            "is_current": bool(blob.is_current_version),
            "version_id": blob.version_id,
        }
        for number, blob in enumerate(versions, start=1)
    ]


# 4. Serve the web page (must be the last line: it answers every path not handled above).
app.mount("/", StaticFiles(directory="static", html=True), name="static")
