from typing import Any, List, Dict, Optional, Callable

import httpx
from loguru import logger

from universal_mcp.applications import APIApplication
from universal_mcp.integrations import Integration
from universal_mcp.exceptions import NotAuthorizedError

class GhostContentApp(APIApplication):
    """
    Universal MCP Application for interacting with the Ghost Content API.
    Credentials (Admin Domain, Content API Key, API Version) are loaded lazily
    when an API tool is first executed.
    """
    def __init__(self, integration: Integration = None, **kwargs) -> None:
        """
        Initializes the GhostContentApp. Basic setup only, credential loading is deferred.
        """
        super().__init__(name="ghost-content", integration=integration, **kwargs)
        # Initialize attributes to defaults or None. They will be populated by _load_credentials.
        self._api_key: Optional[str] = None
        self._api_version: str = "v5.0"  # Default based on Ghost documentation examples
        self.base_url: Optional[str] = None # Will be set based on admin_domain later
        self._credentials_loaded: bool = False # Flag to ensure credentials load only once

        logger.debug("GhostContentApp initialized. Credentials will be loaded on first API call.")

    def _load_credentials(self) -> bool:
        """
        Loads credentials from the integration and sets up API key, version, and base URL.
        This method is designed to run only once per instance.

        Returns:
            bool: True if credentials were loaded successfully (or already loaded),
                  False if loading failed (e.g., no integration, missing keys).
        """
        if self._credentials_loaded:
            return True # Already loaded, skip

        logger.debug("Attempting to load Ghost Content API credentials...")

        if not self.integration:
            logger.error("Ghost Content API integration not configured. Cannot load credentials.")
            return False

        try:
            credentials = self.integration.get_credentials()
        except NotAuthorizedError as e:
             # Handle cases where AgentRIntegration returns an authorization URL/message
            logger.error(f"Authorization required or credentials unavailable for Ghost Content API: {e.message}")
            return False
        except Exception as e:
            logger.error(f"Failed to get credentials from integration: {e}", exc_info=True)
            return False

        admin_domain = credentials.get("GHOST_ADMIN_DOMAIN")
        self._api_key = credentials.get("GHOST_CONTENT_API_KEY")
        # Use GHOST_API_VERSION from creds if available, else keep default
        self._api_version = credentials.get("GHOST_API_VERSION", self._api_version)

        missing_creds = []
        if not admin_domain:
            missing_creds.append("GHOST_ADMIN_DOMAIN")
        else:
            # Set base_url only if admin_domain is present
            self.base_url = f"https://{admin_domain.rstrip('/')}/ghost/api/content/"

        if not self._api_key:
            missing_creds.append("GHOST_CONTENT_API_KEY")

        if missing_creds:
            logger.error(f"Missing required Ghost Content API credentials in integration: {', '.join(missing_creds)}")
            # Mark as loaded to prevent retrying, but loading essentially failed.
            self._credentials_loaded = True
            return False
        else:
            logger.info(
                f"Ghost Content API credentials loaded successfully. "
                f"Base URL: {self.base_url}, API Version: {self._api_version}"
            )
            self._credentials_loaded = True
            return True

    def _get_headers(self) -> dict[str, str]:
        """
        Override to provide specific headers for the Ghost Content API.
        Content API Key is a query parameter, not an Authorization header.
        Uses the API version loaded by _load_credentials.
        """
        headers = super()._get_headers() # Get any headers from base class
        # Remove Authorization if base class adds it, as Content API uses key in params
        headers.pop("Authorization", None)
        # _api_version will be default or loaded by _load_credentials before this is called
        headers["Accept-Version"] = self._api_version
        logger.debug(f"GhostContentApp generated headers: {headers}")
        return headers

    def _prepare_params(self, custom_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Prepares the query parameters, always including the Content API Key.
        Assumes _api_key has been populated by _load_credentials.
        Removes None values from custom_params and formats lists as comma-separated strings.
        """
        # This check now happens *after* _load_credentials should have run
        if not self._api_key:
            logger.error("Ghost Content API key is not available (was not loaded successfully).")
            # Raise ValueError here, caught by _execute_get_request
            raise ValueError("Ghost Content API key is missing or could not be loaded.")

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
        """
        Helper to execute GET request. Ensures credentials are loaded first,
        then prepares params and handles common responses.
        """
        # --- LAZY LOADING TRIGGER ---
        # Attempt to load credentials if they haven't been loaded yet.
        if not self._credentials_loaded:
            if not self._load_credentials():
                # If loading fails, return an informative error immediately.
                return "Error: Failed to load Ghost Content API credentials. Check integration configuration and logs."
        # --- End Lazy Loading ---

        # Check if base_url was successfully set after loading credentials
        if not self.base_url:
            logger.error("Base URL for GhostContentApp is not configured (GHOST_ADMIN_DOMAIN likely missing or failed to load).")
            return "Error: GhostContentApp base URL is not configured. Check GHOST_ADMIN_DOMAIN credential."

        try:
            # _prepare_params will raise ValueError if API key is still missing after load attempt
            prepared_params = self._prepare_params(params)

            # Call the actual HTTP GET method from the base APIApplication class
            # self.client property in APIApplication ensures httpx.Client is initialized
            # with correct base_url (now set) and headers (from _get_headers)
            response = self.client.get(endpoint.lstrip('/'), params=prepared_params)
            response.raise_for_status() # Check for HTTP errors (4xx, 5xx)
            return response.json()

        except httpx.HTTPStatusError as e:
            error_message = f"Error fetching {endpoint}: {e.response.status_code}"
            try:
                error_details = e.response.json()
                # Attempt to extract Ghost-specific error details if available
                ghost_errors = error_details.get("errors", [])
                if ghost_errors:
                    err_msgs = [f"{err.get('message', 'Unknown Ghost error')} (type: {err.get('type', 'N/A')})" for err in ghost_errors]
                    error_message += f" - Ghost API Errors: {'; '.join(err_msgs)}"
                else: # Fallback to raw JSON if no standard 'errors' field
                    error_message += f" - {error_details}"
            except Exception: # If response is not JSON or JSON parsing fails
                error_message += f" - {e.response.text}"
            logger.error(error_message)
            return error_message
        except ValueError as ve: # Catch missing API key from _prepare_params
            logger.error(f"Configuration error during request to {endpoint}: {ve}")
            return f"Configuration error: {ve}"
        except httpx.RequestError as re: # Catch connection errors, timeouts etc.
             logger.error(f"HTTP Request error during Ghost Content API call for {endpoint}: {re}")
             return f"Network or request error fetching {endpoint}: {type(re).__name__} - {re}"
        except Exception as e:
            logger.error(f"Unexpected error during Ghost Content API call for {endpoint}: {e}", exc_info=True)
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
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order
        }
        return self._execute_get_request("tiers/", params)

    # --- Settings Tool ---
    def browse_settings(self) -> Any:
        """Browse site settings."""
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
