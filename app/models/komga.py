from __future__ import annotations
from typing import Any, Dict, Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class AgeRestrictionDto(BaseModel):
    age: int
    restriction: str = "ALLOW_ONLY"


class UserDto(BaseModel):
    id: str
    email: str
    roles: List[str] = Field(default_factory=lambda: ["ROLE_USER", "ROLE_FILE_DOWNLOAD", "ROLE_PAGE_STREAMING"])
    sharedAllLibraries: bool = True
    sharedLibrariesIds: List[str] = Field(default_factory=list)
    labelsAllow: List[str] = Field(default_factory=list)
    labelsExclude: List[str] = Field(default_factory=list)
    ageRestriction: Optional[AgeRestrictionDto] = None


class LibraryDto(BaseModel):
    id: str
    name: str
    root: str = ""
    importComicInfoBook: bool = True
    importComicInfoSeries: bool = True
    importComicInfoCollection: bool = True
    importComicInfoReadList: bool = True
    importComicInfoSeriesAppendVolume: bool = True
    importEpubBook: bool = True
    importEpubSeries: bool = True
    importMylarSeries: bool = True
    importLocalArtwork: bool = True
    importBarcodeIsbn: bool = True
    scanForceModifiedTime: bool = False
    scanInterval: str = "EVERY_6_HOURS"
    scanOnStartup: bool = False
    scanCbx: bool = True
    scanPdf: bool = True
    scanEpub: bool = True
    scanDirectoryExclusions: List[str] = Field(default_factory=list)
    repairExtensions: bool = False
    convertToCbz: bool = False
    emptyTrashAfterScan: bool = False
    seriesCover: str = "FIRST"
    seriesCoverSort: str = "FIRST"
    seriesPaging: str = "DEFAULT"
    hashFiles: bool = False
    hashPages: bool = False
    hashKoreader: bool = False
    analyzeDimensions: bool = True
    oneshotsDirectory: Optional[str] = None
    unavailable: bool = False


class AuthorDto(BaseModel):
    name: str
    role: str = "writer"


class BookMetadataDto(BaseModel):
    title: str = ""
    titleLock: bool = False
    summary: str = ""
    summaryLock: bool = False
    number: str = "1"
    numberLock: bool = False
    numberSort: float = 1.0
    numberSortLock: bool = False
    releaseDate: Optional[str] = None
    releaseDateLock: bool = False
    authors: List[AuthorDto] = Field(default_factory=list)
    authorsLock: bool = False
    tags: List[str] = Field(default_factory=list)
    tagsLock: bool = False
    isbn: str = ""
    isbnLock: bool = False
    links: List[dict] = Field(default_factory=list)
    linksLock: bool = False
    created: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    lastModified: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")


class MediaDto(BaseModel):
    status: str = "READY"
    mediaType: str = "application/x-cbz"
    pagesCount: int = 0
    comment: str = ""
    mediaProfile: str = "DIVINA"
    epubDivinaCompatible: bool = False
    epubIsKepub: bool = False


class ReadProgressDto(BaseModel):
    page: int
    completed: bool
    readDate: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    created: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    lastModified: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    deviceId: str = ""
    deviceName: str = ""


class ReadProgressUpdateDto(BaseModel):
    page: Optional[int] = None
    completed: Optional[bool] = None


class BookDto(BaseModel):
    id: str
    seriesId: str
    seriesTitle: str
    libraryId: str
    name: str
    url: str = ""
    number: int = 1
    created: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    lastModified: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    fileLastModified: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    sizeBytes: int = 0
    size: str = "0 B"
    media: MediaDto
    metadata: BookMetadataDto
    readProgress: Optional[ReadProgressDto] = None
    deleted: bool = False
    fileHash: str = ""
    oneshot: bool = False


class SeriesMetadataDto(BaseModel):
    status: str = "ONGOING"
    statusLock: bool = False
    created: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    lastModified: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    title: str = ""
    titleLock: bool = False
    titleSort: str = ""
    titleSortLock: bool = False
    summary: str = ""
    summaryLock: bool = False
    readingDirection: str = "LEFT_TO_RIGHT"
    readingDirectionLock: bool = False
    publisher: str = ""
    publisherLock: bool = False
    ageRating: Optional[int] = None
    ageRatingLock: bool = False
    language: str = "en"
    languageLock: bool = False
    genres: List[str] = Field(default_factory=list)
    genresLock: bool = False
    tags: List[str] = Field(default_factory=list)
    tagsLock: bool = False
    totalBookCount: int = 0
    totalBookCountLock: bool = False
    sharingLabels: List[str] = Field(default_factory=list)
    sharingLabelsLock: bool = False
    links: List[dict] = Field(default_factory=list)
    linksLock: bool = False
    alternateTitles: List[dict] = Field(default_factory=list)
    alternateTitlesLock: bool = False


class BookMetadataAggregationDto(BaseModel):
    authors: List[AuthorDto] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    releaseDate: Optional[str] = None
    summary: str = ""
    summaryNumber: str = ""
    created: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    lastModified: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")


class SeriesDto(BaseModel):
    id: str
    libraryId: str
    name: str
    url: str = ""
    created: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    lastModified: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    fileLastModified: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    booksCount: int = 0
    booksReadCount: int = 0
    booksUnreadCount: int = 0
    booksInProgressCount: int = 0
    metadata: SeriesMetadataDto
    booksMetadata: BookMetadataAggregationDto
    deleted: bool = False
    oneshot: bool = False


class PageDto(BaseModel):
    number: int
    fileName: str
    mediaType: str = "image/jpeg"
    width: Optional[int] = None
    height: Optional[int] = None
    sizeBytes: Optional[int] = None
    size: Optional[str] = None


class CollectionDto(BaseModel):
    id: str
    name: str
    ordered: bool = False
    seriesIds: List[str] = Field(default_factory=list)
    createdDate: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    lastModifiedDate: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    filtered: bool = False


class ReadListDto(BaseModel):
    id: str
    name: str
    summary: str = ""
    ordered: bool = False
    bookIds: List[str] = Field(default_factory=list)
    createdDate: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    lastModifiedDate: str = Field(default_factory=lambda: "2026-01-01T00:00:00Z")
    filtered: bool = False


class PageableDto(BaseModel, Generic[T]):
    content: List[T]
    pageable: dict = Field(default_factory=lambda: {"pageNumber": 0, "pageSize": 20, "sort": {"empty": True, "sorted": False, "unsorted": True}, "offset": 0, "paged": True, "unpaged": False})
    totalElements: int
    totalPages: int
    last: bool
    first: bool
    size: int
    number: int
    numberOfElements: int
    empty: bool
    sort: dict = Field(default_factory=lambda: {"empty": True, "sorted": False, "unsorted": True})


def build_pageable(
    content: List[T],
    page: int,
    size: int,
    total_elements: int,
    unpaged: bool = False,
) -> PageableDto[T]:
    if unpaged:
        total_pages = 1 if total_elements > 0 else 0
        return PageableDto[T](
            content=content,
            pageable={
                "pageNumber": 0,
                "pageSize": total_elements,
                "sort": {"empty": True, "sorted": False, "unsorted": True},
                "offset": 0,
                "paged": False,
                "unpaged": True,
            },
            totalElements=total_elements,
            totalPages=total_pages,
            last=True,
            first=True,
            size=len(content),
            number=0,
            numberOfElements=len(content),
            empty=len(content) == 0,
            sort={"empty": True, "sorted": False, "unsorted": True},
        )

    total_pages = (total_elements + size - 1) // size if size > 0 else (1 if total_elements > 0 else 0)
    is_first = page == 0
    is_last = total_elements == 0 or page >= total_pages - 1
    return PageableDto[T](
        content=content,
        pageable={
            "pageNumber": page,
            "pageSize": size,
            "sort": {"empty": True, "sorted": False, "unsorted": True},
            "offset": page * size,
            "paged": True,
            "unpaged": False,
        },
        totalElements=total_elements,
        totalPages=total_pages,
        last=is_last,
        first=is_first,
        size=size,
        number=page,
        numberOfElements=len(content),
        empty=len(content) == 0,
        sort={"empty": True, "sorted": False, "unsorted": True},
    )


class ClaimStatusDto(BaseModel):
    isClaimed: bool = True


class ClientSettingDto(BaseModel):
    value: str
    allowUnauthorized: bool = False


class ClientSettingGlobalUpdateDto(BaseModel):
    value: str
    allowUnauthorized: bool = False


class ClientSettingUserUpdateDto(BaseModel):
    value: str


class OAuth2ClientDto(BaseModel):
    name: str
    registrationId: str


class ApiKeyDto(BaseModel):
    id: str
    key: str
    comment: Optional[str] = None
    created: Optional[str] = None
    lastUsed: Optional[str] = None


class R2Device(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None


class R2Progression(BaseModel):
    modified: Optional[str] = None
    device: Optional[R2Device] = None
    locator: Optional[Dict[str, Any]] = None


class TachiyomiReadProgressV2Dto(BaseModel):
    booksCount: int = 0
    booksInProgressCount: int = 0
    booksReadCount: int = 0
    booksUnreadCount: int = 0
    lastReadContinuousNumberSort: float = 0.0
    maxNumberSort: float = 0.0


class TachiyomiReadProgressUpdateV2Dto(BaseModel):
    lastBookNumberSortRead: float

