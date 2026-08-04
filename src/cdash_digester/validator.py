"""
CDASH Validator Module

Validates CDASH resources (Places, Item Sets) using the Omeka-S REST API,
with offline fallback to previously cached batch-database records.

PYTHONPATH=src python -m cdash_digester.validator place 196223



"""

import json
import requests
from typing import Tuple

# ---------------------------------------------------------------------------
# API configuration
# ---------------------------------------------------------------------------

_API_BASE = "https://cdash.cambridgema.gov/api"

_RESOURCE_TEMPLATES = {
    "place":  4,
    "folder": 8,
    "doc":    5,
}

_ENDPOINTS = {
    "place":  f"{_API_BASE}/items",
    "folder": f"{_API_BASE}/item_sets",
    "doc":    f"{_API_BASE}/items",
}


# ---------------------------------------------------------------------------
# CDASHValidator
# ---------------------------------------------------------------------------

class CDASHValidator:
    """Validates CDASH resources against the Omeka-S REST API."""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------- generic validation

    def validate_resource(self, resource_type: str,
                          resource_id: int) -> Tuple[str, str]:
        """Validate any resource type. Returns (status, title)."""
        if resource_type not in _RESOURCE_TEMPLATES:
            return f"ERROR: Unknown resource type '{resource_type}'", "Undefined"
        try:
            endpoint = _ENDPOINTS[resource_type]
            template_id = _RESOURCE_TEMPLATES[resource_type]
            params = {"id": resource_id, "resource_template_id": template_id}
            response = self.session.get(endpoint, params=params,
                                        timeout=self.timeout)
            total = int(response.headers.get("Omeka-S-Total-Results", "0"))
            if response.status_code != 200:
                return f"ERROR: HTTP {response.status_code}", "Undefined"
            if total == 0:
                return (
                    f"Not found: no {resource_type} with ID {resource_id}",
                    "Undefined",
                )
            data = response.json()
            title = self._extract_title(data, resource_type)
            return f"Valid CDASH {resource_type}", title
        except requests.exceptions.RequestException as e:
            return f"ERROR: Request failed — {e}", "Undefined"
        except json.JSONDecodeError:
            return "ERROR: Invalid JSON response", "Undefined"
        except Exception as e:
            return f"ERROR: {e}", "Undefined"

    def _extract_title(self, data: list, resource_type: str) -> str:
        if not data:
            return "Undefined"
        item = data[0]
        try:
            if resource_type == "place":
                return item["cdash:placeItem"][0]["@value"]
            if resource_type in ("folder", "doc"):
                return item.get("o:title", "Undefined")
        except (KeyError, IndexError, TypeError):
            pass
        return "Undefined"

    # -------------------------------------------- place with DB integration

    def _extract_place_properties(self, data: list) -> dict:
        """Extract full place properties from an API response."""
        if not data:
            return {}
        item = data[0]

        def _val(key):
            try:
                return item[key][0]["@value"]
            except (KeyError, IndexError, TypeError):
                return None

        def _multi(key):
            try:
                vals = []
                for v in item.get(key, []):
                    if "@value" in v:
                        vals.append(v["@value"])
                    elif "o:id" in v:
                        vals.append(str(v["o:id"]))
                return ", ".join(vals) or None
            except (KeyError, TypeError):
                return None

        def _coord(key):
            try:
                return float(item["o-module-mapping:marker"][0][key])
            except (KeyError, IndexError, TypeError, ValueError):
                return None

        return {
            "place_name":   _val("cdash:placeItem"),
            "place_type":   _val("cdash:placeType"),
            "house_num":    _val("cdash:houseNum"),
            "street_name":  _val("cdash:streetName"),
            "street_sort":  _val("cdash:streetSort"),
            "neighborhood": _multi("cdash:Neighborhood"),
            "chc_dist":     _multi("cdash:chcDist"),
            "item_set_ids": _multi("o:item_set"),
            "lat":          _coord("o-module-mapping:lat"),
            "lon":          _coord("o-module-mapping:lng")
        }

    def validate_place(self, place_id: int) -> Tuple[str, dict]:
        """Validate a place ID via the Omeka-S API.

        Returns (status, properties) where properties is a dict with all
        place fields (place_name, place_type, house_num, …) on success,
        or an empty dict on failure.  The caller (cdash_place) is
        responsible for caching and DB registration.
        """
        try:
            params = {
                "id": place_id,
                "resource_template_id": _RESOURCE_TEMPLATES["place"],
            }
            response = self.session.get(_ENDPOINTS["place"], params=params,
                                        timeout=self.timeout)
            total = int(response.headers.get("Omeka-S-Total-Results", "0"))
            if response.status_code != 200 or total == 0:
                return f"Not found: place ID {place_id}", {}
            props = self._extract_place_properties(response.json())
            return "Valid CDASH place", props
        except requests.exceptions.RequestException as e:
            return f"ERROR: API unreachable — {e}", {}
        except Exception as e:
            return f"ERROR: {e}", {}

    # --------------------------------------------- folder validation

    def validate_folder(self, item_set_id: int) -> Tuple[str, str]:
        """Validate an Item Set ID via the Omeka-S API.

        Returns (status, folder_name).  The caller (cdash_folder) is
        responsible for caching and DB registration.
        """
        try:
            params = {
                "id": item_set_id,
                "resource_template_id": _RESOURCE_TEMPLATES["folder"],
            }
            response = self.session.get(_ENDPOINTS["folder"], params=params,
                                        timeout=self.timeout)
            total = int(response.headers.get("Omeka-S-Total-Results", "0"))
            if response.status_code != 200 or total == 0:
                return f"Not found: item set ID {item_set_id}", ""
            name = self._extract_title(response.json(), "folder")
            return "Valid CDASH folder", name
        except requests.exceptions.RequestException as e:
            return f"ERROR: API unreachable — {e}", ""
        except Exception as e:
            return f"ERROR: {e}", ""

    def close(self):
        self.session.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    def _usage():
        print("Usage: python -m cdash_digester.validator <resource_type> <id>")
        print("       resource_type: place | folder | doc")
        print("Example: python -m cdash_digester.validator place 196223")
        sys.exit(1)

    if len(sys.argv) != 3:
        _usage()

    resource_type = sys.argv[1].lower()
    try:
        resource_id = int(sys.argv[2])
    except ValueError:
        print(f"ERROR: id must be an integer, got '{sys.argv[2]}'")
        _usage()

    v = CDASHValidator()
    try:
        if resource_type == "place":
            status, props = v.validate_place(resource_id)
            print(f"Status: {status}")
            if props:
                for k, val in props.items():
                    print(f"  {k:<16s}: {val}")
        elif resource_type in ("folder", "doc"):
            status, title = v.validate_folder(resource_id) \
                if resource_type == "folder" \
                else v.validate_resource("doc", resource_id)
            print(f"Status: {status}")
            print(f"  title: {title}")
        else:
            print(f"ERROR: unknown resource type '{resource_type}'")
            _usage()
    finally:
        v.close()
