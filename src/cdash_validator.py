"""
CDASH Validator Module

Validates and retrieves property values for CDASH resources (Places, Documents, Folders)
using the Omeka REST API.

Usage:
    from cdash_validator import CDASHValidator
    
    validator = CDASHValidator()
    status, title = validator.validate_resource('place', 100641)
    status, title = validator.validate_resource('folder', 226525)
"""

import requests
import json
from typing import Tuple, Optional, Dict, Any


class CDASHValidator:
    """Validates CDASH resources against the Omeka REST API."""
    
    # API configuration
    CDASH_API_BASE = "https://cdash.cambridgema.gov/api"
    
    # Resource template IDs in Omeka
    RESOURCE_TEMPLATES = {
        'place': 4,
        'doc': 5,
        'folder': 8
    }
    
    # API endpoints
    ENDPOINTS = {
        'place': f"{CDASH_API_BASE}/items",
        'doc': f"{CDASH_API_BASE}/items",
        'folder': f"{CDASH_API_BASE}/item_sets"
    }
    
    def __init__(self, timeout: int = 10):
        """
        Initialize validator.
        
        Args:
            timeout: Request timeout in seconds (default: 10)
        """
        self.timeout = timeout
        self.session = requests.Session()
    
    def validate_resource(self, resource_type: str, resource_id: int) -> Tuple[str, str]:
        """
        Validate a CDASH resource and retrieve its title.
        
        Args:
            resource_type: Type of resource ('place', 'folder', 'doc')
            resource_id: Numeric ID of the resource
        
        Returns:
            Tuple of (validation_status, resource_title)
                - validation_status: Status message (e.g., "Valid CDASH place")
                - resource_title: Title/name of the resource if found
        """
        if resource_type not in self.RESOURCE_TEMPLATES:
            return f"ERROR: Unknown resource type '{resource_type}'", "Undefined"
        
        try:
            endpoint = self.ENDPOINTS[resource_type]
            template_id = self.RESOURCE_TEMPLATES[resource_type]
            
            params = {
                "id": resource_id,
                "resource_template_id": template_id
            }
            
            response = self.session.get(endpoint, params=params, timeout=self.timeout)
            total_results = response.headers.get('Omeka-S-Total-Results', '0')
            
            # Handle HTTP errors
            if response.status_code != 200:
                return f"ERROR: HTTP {response.status_code}", "Undefined"
            
            # Check if resource was found
            if int(total_results) == 0:
                return f"Not found: No {resource_type} with ID {resource_id}", "Undefined"
            
            # Parse response and extract title
            data = response.json()
            resource_title = self._extract_title(data, resource_type)
            # For debugging and inspection of the json schema.
            print(json.dumps(response.json(), indent=2))
            return f"Valid CDASH {resource_type}", resource_title
        
        except requests.exceptions.RequestException as e:
            return f"ERROR: Request failed - {str(e)}", "Undefined"
        except json.JSONDecodeError:
            return "ERROR: Invalid JSON response", "Undefined"
        except Exception as e:
            return f"ERROR: {str(e)}", "Undefined"
    
    def _extract_title(self, data: list, resource_type: str) -> str:
        """
        Extract resource title from API response.
        
        Args:
            data: Parsed JSON response
            resource_type: Type of resource
        
        Returns:
            Title string or "Undefined" if not found
        """
        if not data or len(data) == 0:
            return "Undefined"
        
        item = data[0]
        
        try:
            if resource_type == "place":
                return item["cdash:placeItem"][0]["@value"]
            elif resource_type in ("folder", "doc"):
                return item.get("o:title", "Undefined")
        except (KeyError, IndexError, TypeError):
            pass
        
        return "Undefined"
    
    def close(self):
        """Close the session."""
        self.session.close()


def validate_place(place_id: int) -> Tuple[str, str]:
    """Convenience function to validate a place."""
    validator = CDASHValidator()
    try:
        return validator.validate_resource('place', place_id)
    finally:
        validator.close()


def validate_folder(folder_id: int) -> Tuple[str, str]:
    """Convenience function to validate a folder."""
    validator = CDASHValidator()
    try:
        return validator.validate_resource('folder', folder_id)
    finally:
        validator.close()


def validate_doc(doc_id: int) -> Tuple[str, str]:
    """Convenience function to validate a document."""
    validator = CDASHValidator()
    try:
        return validator.validate_resource('doc', doc_id)
    finally:
        validator.close()


# Example usage / testing
if __name__ == "__main__":
    validator = CDASHValidator()
    
    # Test place validation
    # print("Testing place validation...")
    # status, title = validator.validate_resource("place", 100641)
    # print(f"Status: {status}, Title: {title}\n")
    
    # Test folder validation (commented out - uncomment to test)
    # print("Testing folder validation...")
    # status, title = validator.validate_resource("folder", 226525)
    # print(f"Status: {status}, Title: {title}\n")
    
    # Test invalid resource ID
    print("Testing place ID...")
    status, title = validator.validate_resource("place", 196223)
    print(f"Status: {status}, Title: {title}\n")
    
    validator.close()



