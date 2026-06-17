"""ValidationService — folder/place validation with the persistent caches.

Wraps the cache-then-API resolution that used to live in Digester._scan_folder
(folder names) and Digester._ensure_place (places). The validator and BatchDB
remain cache-unaware; all cache logic lives here.
"""

from typing import Optional, Tuple

from ..cdash_objects import PLACE_PROP_KEYS

PLACE_FOLDER_MISMATCH_NOTE = "Place_ID is not associated with this folder in CDASH."


class ValidationService:
    def __init__(self, dig):
        self._dig = dig

    # ------------------------------------------------------------------ folder

    def resolve_folder_name(self, item_set_id: int,
                            folder_index: int) -> Tuple[Optional[str], bool]:
        """Resolve a folder's CDASH name.

        Returns (cdash_folder_name | None, name_ready). The persistent folder
        cache is consulted first; on a miss the validator API is called and a
        valid result is cached. On failure returns (None, False) and the caller
        falls back to the parsed slug. Only valid results are cached, so an
        invalid folder is retried via the API on the next scan.
        """
        db = self._dig.db
        cache = db.get_folder_cache(item_set_id)
        if cache and cache["status"] == "valid":
            name = cache["cdash_folder_name"]
            self._dig.log(f"  Validated folder (cached): {name}", "info")
            return name, True

        api_status, api_name = self._dig._validator.validate_folder(item_set_id)
        if "Valid" in api_status:
            self._dig.log(f"  Validated folder: {api_name}", "info")
            db.upsert_folder_cache(item_set_id, api_name, folder_index, "valid")
            return api_name, True

        self._dig.log(f"  Folder ID {item_set_id}: {api_status}", "warning")
        return None, False

    # ------------------------------------------------------------------- place

    def ensure_place(self, place_id: int) -> Tuple[str, str]:
        """Ensure the working cdash_place row for place_id is populated.

        Resolution order, cheapest first:
          1. cdash_place         — already populated during this scan.
          2. cdash_place_cache   — persistent validator cache (no network).
          3. validator API       — on a miss; result is written to both the
                                    cache and the working table.
        Only valid results are cached, so an invalid place is retried via the
        API on the next scan.  Returns (status, place_name).
        """
        dig = self._dig
        if not dig.db:
            status, props = dig._validator.validate_place(place_id)
            return status, (props.get("place_name", "") if props else "")

        existing = dig.db.get_place(place_id)
        if existing:
            return "Valid CDASH place (cached)", existing["place_name"]

        cache = dig.db.get_place_cache(place_id)
        if cache and cache["status"] == "valid":
            props = {k: cache[k] for k in PLACE_PROP_KEYS}
            dig.db.upsert_place(place_item_id=place_id, **props)
            return "Valid CDASH place (cached)", cache["place_name"]

        status, props = dig._validator.validate_place(place_id)
        if "Valid" in status and props:
            place_name = props.get("place_name", "")
            dig.db.upsert_place_cache(place_id, props, "valid")
            dig.db.upsert_place(place_item_id=place_id, **props)
            return status, place_name
        return status, ""

    def place_associated_with_folder(self, place_id: int,
                                     item_set_id: int) -> bool:
        """True if the place lists this folder's item_set_id (or lists none).

        Empty/None item_set_ids → True (skip the check). Only returns False when
        item_set_ids has entries and this folder's id is not among them. Relies
        on ensure_place having already populated the working cdash_place row.
        """
        place = self._dig.db.get_place(place_id) if self._dig.db else None
        raw = (place.get("item_set_ids") or "").strip() if place else ""
        if not raw:
            return True
        ids = {part.strip() for part in raw.split(",") if part.strip()}
        return str(item_set_id) in ids
