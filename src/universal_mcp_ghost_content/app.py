from typing import Any, List, Dict, Optional, Callable

import httpx
from loguru import logger

from universal_mcp.applications import APIApplication
from universal_mcp.integrations import Integration

class GhostContentApp(APIApplication):
    """
    Base class for Universal MCP Applications.
    """
    def __init__(self, integration: Integration = None, **kwargs) -> None:
        super().__init__(name="ghost-content", integration=integration, **kwargs)
        self._api_key: Optional[str] = None
        self._api_version: str = "v5.0"  # Default based on Ghost documentation examples

        if not self.integration:
            logger.error(
                "Ghost Content API integration not configured. "
                "Admin domain, Content API key, and API version will use defaults or be missing."
            )
            # Allow initialization for potential use without integration, though API calls will fail.
        else:
            credentials = self.integration.get_credentials()
            admin_domain = credentials.get("GHOST_ADMIN_DOMAIN")
            self._api_key = credentials.get("GHOST_CONTENT_API_KEY")
            # Use GHOST_API_VERSION from creds if available, else keep default
            self._api_version = credentials.get("GHOST_API_VERSION", self._api_version)

            if not admin_domain:
                logger.error("GHOST_ADMIN_DOMAIN not found in Ghost Content API integration credentials.")
                # Base URL will not be properly set, API calls will likely fail or use incorrect URL.
            else:
                self.base_url = f"https://{admin_domain.rstrip('/')}/ghost/api/content/"
            
            if not self._api_key:
                logger.error("GHOST_CONTENT_API_KEY not found in Ghost Content API integration credentials.")
                # API calls will fail without the key.

        logger.info(
            f"GhostContentApp initialized with base_url: {getattr(self, 'base_url', 'Not Set')} "
            f"and API version: {self._api_version}"
        )

    def _get_headers(self) -> dict[str, str]:
        """
        Override to provide specific headers for the Ghost Content API.
        Content API Key is a query parameter, not an Authorization header.
        """
        headers = super()._get_headers() # Get any headers from base class
        # Remove Authorization if base class adds it, as Content API uses key in params
        headers.pop("Authorization", None)
        headers["Accept-Version"] = self._api_version
        logger.debug(f"GhostContentApp generated headers: {headers}")
        return headers

    def _prepare_params(self, custom_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Prepares the query parameters, always including the Content API Key.
        Removes None values from custom_params and formats lists as comma-separated strings.
        """
        if not self._api_key:
            logger.error("Ghost Content API key is not available.")
            raise ValueError("Ghost Content API key is missing. Cannot make requests.")

        final_params: Dict[str, Any] = {"key": self._api_key}
        if custom_params:
            for k, v in custom_params.items():
                if v is not None:
                    if isinstance(v, bool):
                        final_params[k] = str(v).lower()
                    elif isinstance(v, list):
                        final_params[k] = ",".join(map(str, v))
                    else:
                        final_params[k] = str(v) # Ensure all params are strings
        return final_params

    def _execute_get_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Helper to execute GET request and handle common responses."""
        if not hasattr(self, 'base_url') or not self.base_url:
            logger.error("Base URL for GhostContentApp is not configured.")
            return "Error: GhostContentApp base URL is not configured. Check GHOST_ADMIN_DOMAIN."
        try:
            prepared_params = self._prepare_params(params)
            response = self._get(endpoint.lstrip('/'), params=prepared_params) # _get is from APIApplication
            return response.json()
        except httpx.HTTPStatusError as e:
            error_message = f"Error fetching {endpoint}: {e.response.status_code}"
            try:
                error_details = e.response.json()
                error_message += f" - {error_details}"
            except Exception: # If response is not JSON
                error_message += f" - {e.response.text}"
            logger.error(error_message)
            return error_message
        except ValueError as ve: # Catch missing API key from _prepare_params
            logger.error(f"ValueError during request to {endpoint}: {ve}")
            return f"Configuration error: {ve}"
        except Exception as e:
            logger.error(f"Unexpected error during Ghost Content API call for {endpoint}: {e}")
            return f"Unexpected error fetching {endpoint}: {type(e).__name__} - {e}"

    # --- Posts Tools ---
    def browse_posts(self, include: Optional[List[str]] = None, fields: Optional[List[str]] = None,
                     filter: Optional[str] = None, limit: Optional[int] = None,
                     page: Optional[int] = None, order: Optional[str] = None,
                     formats: Optional[List[str]] = None) -> Any:
        """Browse posts. Parameters: include, fields, filter, limit, page, order, formats."""
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order, "formats": formats
        }
        return self._execute_get_request("posts/", params)

    def read_post_by_id(self, id: str, include: Optional[List[str]] = None,
                        fields: Optional[List[str]] = None, formats: Optional[List[str]] = None) -> Any:
        """Read a post by its ID. Parameters: id, include, fields, formats."""
        params = {"include": include, "fields": fields, "formats": formats}
        return self._execute_get_request(f"posts/{id}/", params)

    def read_post_by_slug(self, slug: str, include: Optional[List[str]] = None,
                          fields: Optional[List[str]] = None, formats: Optional[List[str]] = None) -> Any:
        """Read a post by its slug. Parameters: slug, include, fields, formats."""
        params = {"include": include, "fields": fields, "formats": formats}
        return self._execute_get_request(f"posts/slug/{slug}/", params)

    # --- Authors Tools ---
    def browse_authors(self, include: Optional[List[str]] = None, fields: Optional[List[str]] = None,
                       filter: Optional[str] = None, limit: Optional[int] = None,
                       page: Optional[int] = None, order: Optional[str] = None) -> Any:
        """Browse authors. Parameters: include, fields, filter, limit, page, order."""
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order
        }
        return self._execute_get_request("authors/", params)

    def read_author_by_id(self, id: str, include: Optional[List[str]] = None,
                          fields: Optional[List[str]] = None) -> Any:
        """Read an author by ID. Parameters: id, include, fields."""
        params = {"include": include, "fields": fields}
        return self._execute_get_request(f"authors/{id}/", params)

    def read_author_by_slug(self, slug: str, include: Optional[List[str]] = None,
                            fields: Optional[List[str]] = None) -> Any:
        """Read an author by slug. Parameters: slug, include, fields."""
        params = {"include": include, "fields": fields}
        return self._execute_get_request(f"authors/slug/{slug}/", params)

    # --- Tags Tools ---
    def browse_tags(self, include: Optional[List[str]] = None, fields: Optional[List[str]] = None,
                    filter: Optional[str] = None, limit: Optional[int] = None,
                    page: Optional[int] = None, order: Optional[str] = None) -> Any:
        """Browse tags. Parameters: include, fields, filter, limit, page, order."""
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order
        }
        return self._execute_get_request("tags/", params)

    def read_tag_by_id(self, id: str, include: Optional[List[str]] = None,
                       fields: Optional[List[str]] = None) -> Any:
        """Read a tag by ID. Parameters: id, include, fields."""
        params = {"include": include, "fields": fields}
        return self._execute_get_request(f"tags/{id}/", params)

    def read_tag_by_slug(self, slug: str, include: Optional[List[str]] = None,
                         fields: Optional[List[str]] = None) -> Any:
        """Read a tag by slug. Parameters: slug, include, fields."""
        params = {"include": include, "fields": fields}
        return self._execute_get_request(f"tags/slug/{slug}/", params)

    # --- Pages Tools ---
    def browse_pages(self, include: Optional[List[str]] = None, fields: Optional[List[str]] = None,
                     filter: Optional[str] = None, limit: Optional[int] = None,
                     page: Optional[int] = None, order: Optional[str] = None,
                     formats: Optional[List[str]] = None) -> Any:
        """Browse pages. Parameters: include, fields, filter, limit, page, order, formats."""
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order, "formats": formats
        }
        return self._execute_get_request("pages/", params)

    def read_page_by_id(self, id: str, include: Optional[List[str]] = None,
                        fields: Optional[List[str]] = None, formats: Optional[List[str]] = None) -> Any:
        """Read a page by ID. Parameters: id, include, fields, formats."""
        params = {"include": include, "fields": fields, "formats": formats}
        return self._execute_get_request(f"pages/{id}/", params)

    def read_page_by_slug(self, slug: str, include: Optional[List[str]] = None,
                          fields: Optional[List[str]] = None, formats: Optional[List[str]] = None) -> Any:
        """Read a page by slug. Parameters: slug, include, fields, formats."""
        params = {"include": include, "fields": fields, "formats": formats}
        return self._execute_get_request(f"pages/slug/{slug}/", params)

    # --- Tiers Tool ---
    def browse_tiers(self, include: Optional[List[str]] = None, fields: Optional[List[str]] = None,
                     filter: Optional[str] = None, limit: Optional[int] = None,
                     page: Optional[int] = None, order: Optional[str] = None) -> Any:
        """Browse tiers. Parameters: include, fields, filter, limit, page, order."""
        # Note: Content API for Tiers might have limited fields/include options compared to Admin API.
        # The documentation specifically mentions: include=benefits,monthly_price,yearly_price
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order
        }
        return self._execute_get_request("tiers/", params)

    # --- Settings Tool ---
    def browse_settings(self) -> Any:
        """Browse site settings."""
        # The documentation says: "This endpoint doesn’t accept any query parameters."
        # However, the general Content API parameters section lists "include" and "fields" for all endpoints.
        # We will call it without specific parameters here, relying on _prepare_params to add the key.
        return self._execute_get_request("settings/", params=None)

    def list_tools(self) -> List[Callable]:
        """Returns a list of methods exposed as tools for the Ghost Content API."""
        return [
            self.browse_posts,
            self.read_post_by_id,
            self.read_post_by_slug,
            self.browse_authors,
            self.read_author_by_id,
            self.read_author_by_slug,
            self.browse_tags,
            self.read_tag_by_id,
            self.read_tag_by_slug,
            self.browse_pages,
            self.read_page_by_id,
            self.read_page_by_slug,
            self.browse_tiers,
            self.browse_settings,
        ]