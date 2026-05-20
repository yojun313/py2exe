import os
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from packaging.version import Version, InvalidVersion

app = FastAPI(title="App Version Management API")

VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.txt")
DEFAULT_VERSION = "1.0.0"


class VersionUpdateSchema(BaseModel):
    version: str = Field(..., example="1.0.1")


def read_version_file() -> str:
    if not os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(DEFAULT_VERSION)
        return DEFAULT_VERSION
    
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def write_version_file(version: str):
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(version)


@app.get("/api/version", status_code=status.HTTP_200_OK)
async def get_latest_version():
    current_version = read_version_file()
    return {"latest_version": current_version}


@app.post("/api/version", status_code=status.HTTP_200_OK)
async def update_latest_version(payload: VersionUpdateSchema):
    try:
        validated_version = Version(payload.version)
    except InvalidVersion:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid version format. Use semantic versioning (e.g., 1.0.1)."
        )
    
    write_version_file(str(validated_version))
    return {"message": "Version updated successfully", "latest_version": str(validated_version)}


@app.get("/api/version/check", status_code=status.HTTP_200_OK)
async def check_update_required(client_version: str):
    try:
        client_v = Version(client_version)
    except InvalidVersion:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid client version format."
        )
        
    latest_v = Version(read_version_file())
    update_required = latest_v > client_v
    
    return {
        "client_version": str(client_v),
        "latest_version": str(latest_v),
        "update_required": update_required
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("version_server:app", host="0.0.0.0", port=8000, reload=True)