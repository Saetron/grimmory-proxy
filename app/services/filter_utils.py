import logging
from typing import Any, Dict, List, Optional, Set
from fastapi import Request

from app.models.internal import UserSession

logger = logging.getLogger("grimmory_proxy.filter_utils")


def _extract_ids_recursive(data: Any, key_names: Set[str]) -> List[Any]:
    """
    Recursively extracts values associated with key_names from dicts and lists.
    Handles OpenAPI SearchCondition structures (such as 'allOf', 'anyOf',
    and SearchOperatorEqualityString e.g. {"operator": "is", "value": ...}).
    """
    extracted: List[Any] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k in key_names:
                if isinstance(v, list):
                    extracted.extend(v)
                elif isinstance(v, dict) and "value" in v:
                    extracted.append(v["value"])
                elif v is not None:
                    extracted.append(v)
            elif isinstance(v, (dict, list)):
                extracted.extend(_extract_ids_recursive(v, key_names))
    elif isinstance(data, list):
        for item in data:
            extracted.extend(_extract_ids_recursive(item, key_names))
    return extracted


def extract_filter_params(request: Request, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Extracts library_ids, series_ids, search terms, and unpaged flag from query parameters
    and/or JSON request body (supporting both Komga standard SearchCondition schemas
    and flat client variations).
    """
    # 1. Extract Library IDs
    lib_id_keys = {"library_id", "libraryId", "library_ids", "libraryIds"}
    raw_lib_ids: List[Any] = []

    for key in ("library_id", "libraryId", "library_ids", "libraryIds"):
        for val in request.query_params.getlist(key):
            raw_lib_ids.append(val)

    if body:
        raw_lib_ids.extend(_extract_ids_recursive(body, lib_id_keys))

    library_ids: List[int] = []
    for item in raw_lib_ids:
        for part in str(item).split(","):
            part = part.strip()
            if part.isdigit():
                val_int = int(part)
                if val_int not in library_ids:
                    library_ids.append(val_int)

    # 2. Extract Series IDs
    series_id_keys = {"series_id", "seriesId", "series_ids", "seriesIds"}
    raw_series_ids: List[Any] = []

    for key in ("series_id", "seriesId", "series_ids", "seriesIds"):
        for val in request.query_params.getlist(key):
            raw_series_ids.append(val)

    if body:
        raw_series_ids.extend(_extract_ids_recursive(body, series_id_keys))

    series_ids: List[str] = []
    for item in raw_series_ids:
        for part in str(item).split(","):
            part = part.strip()
            if part and part not in series_ids:
                series_ids.append(part)

    # 3. Extract Search Term
    search: Optional[str] = None
    for key in ("search", "searchTerm", "q", "fullTextSearch"):
        if key in request.query_params and request.query_params[key].strip():
            search = request.query_params[key].strip()
            break

    if not search and body:
        for key in ("fullTextSearch", "searchTerm", "search"):
            if key in body and str(body[key]).strip():
                search = str(body[key]).strip()
                break
        if not search and "condition" in body and isinstance(body["condition"], dict):
            cond = body["condition"]
            if "fullTextSearch" in cond and str(cond["fullTextSearch"]).strip():
                search = str(cond["fullTextSearch"]).strip()

    # 4. Extract Unpaged Flag
    unpaged = False
    if "unpaged" in request.query_params:
        unpaged = request.query_params.get("unpaged", "").lower() in ("true", "1")
    elif body and "unpaged" in body:
        unpaged = bool(body["unpaged"])

    return {
        "library_ids": library_ids,
        "series_ids": series_ids,
        "search": search,
        "unpaged": unpaged,
    }


def resolve_effective_library_ids(user: UserSession, requested_lib_ids: List[int]) -> Optional[List[int]]:
    """
    Resolves the effective library IDs for the database query.
    - If user has assigned library restrictions:
        * If specific libraries were requested: returns intersection.
          (Empty list [] means the user has no permission for requested libraries).
        * If no libraries were requested: defaults strictly to user's assigned libraries.
    - If user is admin or has no restrictions:
        * Returns requested_lib_ids if non-empty, otherwise None (all libraries).
    """
    if not user.is_admin and user.assigned_library_ids:
        if requested_lib_ids:
            return [lid for lid in requested_lib_ids if lid in user.assigned_library_ids]
        return user.assigned_library_ids

    return requested_lib_ids if requested_lib_ids else None
