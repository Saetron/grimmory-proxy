from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GrimmoryLoginRequest(BaseModel):
    username: str
    password: str


class GrimmoryLoginResponse(BaseModel):
    accessToken: str
    refreshToken: Optional[str] = None
    expires: int = 7200
    isDefaultPassword: bool = False


class GrimmoryPermissions(BaseModel):
    admin: bool = False
    canDownload: bool = True
    canEditMetadata: bool = False
    demoUser: bool = False


class GrimmoryUser(BaseModel):
    id: int
    username: str
    name: Optional[str] = None
    email: Optional[str] = None
    permissions: GrimmoryPermissions = Field(default_factory=GrimmoryPermissions)
    assignedLibraries: List[Any] = Field(default_factory=list)


class GrimmoryBookMetadataUpdate(BaseModel):
    metadata: Dict[str, Any]
    clearFlags: Dict[str, Any] = Field(default_factory=dict)


class GrimmoryCbxProgress(BaseModel):
    page: int
    percentage: float


class GrimmoryPdfProgress(BaseModel):
    page: int
    percentage: float


class GrimmoryEpubProgress(BaseModel):
    percentage: float
    cfi: Optional[str] = None
    href: Optional[str] = None


class GrimmoryReadProgressRequest(BaseModel):
    bookId: int
    cbxProgress: Optional[GrimmoryCbxProgress] = None
    pdfProgress: Optional[GrimmoryPdfProgress] = None
    epubProgress: Optional[GrimmoryEpubProgress] = None
    dateFinished: Optional[str] = None
