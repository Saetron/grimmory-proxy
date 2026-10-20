import ast
import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from fastapi import Request

from app.models.internal import UserSession

logger = logging.getLogger("grimmory_proxy.filter_utils")


KNOWN_AUTHOR_ROLES: Set[str] = {
    "writer", "penciller", "inker", "colorist", "letterer",
    "cover", "editor", "translator", "assistant",
}


def _parse_author_items(item: Any) -> List[Dict[str, Optional[str]]]:
    """
    Normalizes diverse author representations from Komga queries and conditions
    into structured author filters: [{'name': str, 'role': Optional[str]}].
    """
    results: List[Dict[str, Optional[str]]] = []
    if item is None:
        return results

    if isinstance(item, dict):
        if "value" in item:
            return _parse_author_items(item["value"])
        if "values" in item and isinstance(item["values"], list):
            for v in item["values"]:
                results.extend(_parse_author_items(v))
            return results

        name = str(item.get("name") or "").strip()
        role = str(item.get("role") or "").strip() or None
        if name:
            results.append({"name": name, "role": role})
        return results

    if isinstance(item, list):
        for sub in item:
            results.extend(_parse_author_items(sub))
        return results

    s = str(item).strip()
    if not s:
        return results

    # Support stringified dict/JSON representations
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            parsed = json.loads(s)
            return _parse_author_items(parsed)
        except Exception:
            try:
                parsed = ast.literal_eval(s)
                return _parse_author_items(parsed)
            except Exception:
                pass

    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) == 2 and parts[1].lower() in KNOWN_AUTHOR_ROLES:
            results.append({"name": parts[0], "role": parts[1].lower()})
        else:
            for part in parts:
                if part.lower() not in KNOWN_AUTHOR_ROLES:
                    results.append({"name": part, "role": None})
    else:
        results.append({"name": s, "role": None})

    return results


def _extract_ids_recursive(data: Any, key_names: Set[str]) -> List[Any]:
    """
    Recursively extracts values associated with key_names from dicts and lists.
    Handles OpenAPI SearchCondition structures (such as 'allOf', 'anyOf',
    and SearchOperatorEqualityString e.g. {"operator": "is", "value": ...} or {"values": [...]},
    or nested {"id": ...} / {"ids": [...]}, or AST criteria {"field": ..., "value": ...}).
    """
    extracted: List[Any] = []
    lower_keys = {k.lower() for k in key_names}
    if isinstance(data, dict):
        cond_field = str(data.get("field") or data.get("type") or "").lower()
        if cond_field in lower_keys:
            if "value" in data:
                extracted.append(data["value"])
            if "values" in data and isinstance(data["values"], list):
                extracted.extend(data["values"])

        for k, v in data.items():
            if k.lower() in lower_keys:
                if isinstance(v, list):
                    extracted.extend(v)
                elif isinstance(v, dict):
                    if "value" in v:
                        extracted.append(v["value"])
                    if "values" in v and isinstance(v["values"], list):
                        extracted.extend(v["values"])
                    if "id" in v:
                        extracted.append(v["id"])
                    if "ids" in v and isinstance(v["ids"], list):
                        extracted.extend(v["ids"])
                    if "role" in v or "name" in v:
                        extracted.append(v)
                    extracted.extend(_extract_ids_recursive(v, key_names))
                elif v is not None:
                    extracted.append(v)
            elif isinstance(v, (dict, list)):
                extracted.extend(_extract_ids_recursive(v, key_names))
    elif isinstance(data, list):
        for item in data:
            extracted.extend(_extract_ids_recursive(item, key_names))
    return extracted


def _extract_string_filter_list(
    request: Request,
    body: Optional[Any],
    keys: Set[str],
    dict_keys: Tuple[str, ...] = (),
    uppercase: bool = False,
) -> List[str]:
    raw_items: List[Any] = [
        qv for qk, qv in request.query_params.multi_items() if qk.lower() in keys
    ]
    if body:
        raw_items.extend(_extract_ids_recursive(body, keys))

    results: List[str] = []
    for item in raw_items:
        if isinstance(item, dict) and dict_keys:
            val = next((item.get(dk) for dk in dict_keys if item.get(dk)), None)
            if val:
                s_val = str(val).strip()
                if uppercase:
                    s_val = s_val.upper()
                if s_val and s_val not in results:
                    results.append(s_val)
            continue
        for part in str(item).split(","):
            part = part.strip()
            if uppercase:
                part = part.upper()
            if part and part not in results:
                results.append(part)
    return results


def parse_library_ids_param(
    request: Optional[Request] = None,
    library_id: Any = None,
) -> List[int]:
    """
    Parses library_id query parameters (supporting repeated keys and comma-separated values)
    into a deduplicated list of integers.
    """
    raw_items: List[Any] = []
    if request is not None:
        lib_keys = {"library_id", "library_id[]", "library_ids", "library_ids[]"}
        for qk, qv in request.query_params.multi_items():
            if qk.lower() in lib_keys:
                raw_items.append(qv)
    if library_id is not None:
        if isinstance(library_id, list):
            raw_items.extend(library_id)
        else:
            raw_items.append(library_id)

    parsed_libs: List[int] = []
    for item in raw_items:
        for part in str(item).split(","):
            part = part.strip()
            if part.isdigit():
                val_int = int(part)
                if val_int not in parsed_libs:
                    parsed_libs.append(val_int)
    return parsed_libs


def is_unpaged_request(request: Request, unpaged: bool = False) -> bool:
    """Checks whether a request has unpaged=true via parameter or query string."""
    return unpaged or request.query_params.get("unpaged", "").lower() in ("true", "1")


def extract_filter_params(request: Request, body: Optional[Any] = None) -> Dict[str, Any]:
    """
    Extracts library_ids, series_ids, search terms, read_status, genres, tags, publishers, authors,
    and unpaged flag from query parameters and/or JSON request body (supporting both Komga standard
    SearchCondition schemas and flat client variations).
    """
    # 1. Extract Library IDs
    lib_id_keys = {
        "library_id", "libraryid", "library_ids", "libraryids",
        "library", "libraries", "library_id[]", "libraryid[]", "libraries[]",
    }
    raw_lib_ids: List[Any] = [
        qv for qk, qv in request.query_params.multi_items() if qk.lower() in lib_id_keys
    ]
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
    series_id_keys = {
        "series_id", "seriesid", "series_ids", "seriesids",
        "series", "series_id[]", "seriesid[]", "seriesids[]",
    }
    series_ids = _extract_string_filter_list(request, body, series_id_keys)

    # 3. Extract Search Term
    search_keys = {"search", "searchterm", "q", "fulltextsearch"}
    search: Optional[str] = None
    for qk, qv in request.query_params.items():
        if qk.lower() in search_keys and qv.strip():
            search = qv.strip()
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

    # 3b. Extract Collection IDs
    collection_keys = {
        "collection", "collections", "collection_id", "collection_ids",
        "collection[]", "collections[]", "collection_id[]", "collection_ids[]",
    }
    collection_ids = _extract_string_filter_list(request, body, collection_keys, dict_keys=("id", "value"))

    # 4. Extract Read Status
    read_status_keys = {"read_status", "readstatus", "read_status[]", "readstatus[]"}
    read_status = _extract_string_filter_list(request, body, read_status_keys, uppercase=True)

    # 5. Extract Genres
    genre_keys = {"genre", "genres", "genre[]", "genres[]"}
    genres = _extract_string_filter_list(request, body, genre_keys, dict_keys=("name", "value"))

    # 6. Extract Tags
    tag_keys = {"tag", "tags", "tag[]", "tags[]"}
    tags = _extract_string_filter_list(request, body, tag_keys, dict_keys=("name", "value"))

    # 7. Extract Publishers
    publisher_keys = {"publisher", "publishers", "publisher[]", "publishers[]"}
    publishers = _extract_string_filter_list(request, body, publisher_keys, dict_keys=("name", "value"))

    # 8. Extract Authors
    author_keys = {"author", "authors", "author[]", "authors[]"}
    raw_authors: List[Any] = [
        qv for qk, qv in request.query_params.multi_items() if qk.lower() in author_keys
    ]
    if body:
        raw_authors.extend(_extract_ids_recursive(body, author_keys))
    authors: List[Dict[str, Optional[str]]] = []
    for item in raw_authors:
        for parsed in _parse_author_items(item):
            if parsed not in authors:
                authors.append(parsed)

    # 9. Extract Unpaged Flag
    unpaged = False
    if "unpaged" in request.query_params:
        unpaged = request.query_params.get("unpaged", "").lower() in ("true", "1")
    elif body and "unpaged" in body:
        unpaged = bool(body["unpaged"])

    return {
        "library_ids": library_ids,
        "series_ids": series_ids,
        "collection_ids": collection_ids,
        "search": search,
        "read_status": read_status,
        "genres": genres,
        "tags": tags,
        "publishers": publishers,
        "authors": authors,
        "unpaged": unpaged,
    }


def resolve_effective_library_ids(user: UserSession, requested_lib_ids: List[int]) -> Optional[List[int]]:
    """
    Resolves the effective library IDs for the database query.
    - If user has assigned library restrictions (non-empty assigned_library_ids):
        * If specific libraries were requested: returns intersection.
          (Empty list [] means the user requested libraries they don't have access to).
        * If no libraries were requested: defaults strictly to user's assigned libraries.
    - If user is admin or has NO restrictions (assigned_library_ids is empty/None):
        * Returns requested_lib_ids if non-empty, otherwise None (meaning all libraries).
    """
    if not user.is_admin and user.assigned_library_ids:
        if requested_lib_ids:
            return [lid for lid in requested_lib_ids if lid in user.assigned_library_ids]
        return user.assigned_library_ids

    return requested_lib_ids if requested_lib_ids else None
