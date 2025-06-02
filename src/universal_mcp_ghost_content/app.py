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
        """
        Retrieves and browses posts from a data source based on provided parameters.
        
        Args:
            include: A list of fields to include in the response.
            fields: A list of specific fields to retrieve.
            filter: A string to filter the posts by.
            limit: The maximum number of posts to return.
            page: The page number to start retrieving posts from.
            order: The order in which to retrieve posts.
            formats: A list of formats in which to retrieve posts.
        
        Returns:
            The result of the posts retrieval, which may be in various formats depending on the request parameters.
        
        Raises:
            Exception: An exception might be raised if there is an issue with the request execution, such as network errors or invalid parameters.
        
        Tags:
            browse, fetch, posts, management, important
        """
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order, "formats": formats
        }
        return self._execute_get_request("posts/", params)

    def read_post_by_id(self, id: str, include: Optional[List[str]] = None,
                        fields: Optional[List[str]] = None, formats: Optional[List[str]] = None) -> Any:
        """
        Retrieves a post by its ID, optionally including additional data or specific fields.
        
        Args:
            id: The unique identifier of the post to retrieve.
            include: Optional list of additional data to include in the response.
            fields: Optional list of specific fields to retrieve for the post.
            formats: Optional list of formats for the post data.
        
        Returns:
            The retrieved post data in the specified format.
        
        Tags:
            read, post, management
        """
        params = {"include": include, "fields": fields, "formats": formats}
        return self._execute_get_request(f"posts/{id}/", params)

    def read_post_by_slug(self, slug: str, include: Optional[List[str]] = None,
                          fields: Optional[List[str]] = None, formats: Optional[List[str]] = None) -> Any:
        """
        Retrieves a post by its slug, with optional parameters to specify included data, select specific fields, or request particular data formats.
        
        Args:
            slug: Unique slug identifier of the post to retrieve.
            include: Optional list of related objects to include with the post.
            fields: Optional list of fields to include in the returned post data.
            formats: Optional list of data formats in which to return the post.
        
        Returns:
            The retrieved post data or response payload, as returned by the underlying GET request executor.
        
        Raises:
            Exception: May be raised if the underlying GET request fails, e.g., due to network issues, invalid parameters, or unauthorized access.
        
        Tags:
            read, post, fetch, management
        """
        params = {"include": include, "fields": fields, "formats": formats}
        return self._execute_get_request(f"posts/slug/{slug}/", params)

    # --- Authors Tools ---
    def browse_authors(self, include: Optional[List[str]] = None, fields: Optional[List[str]] = None,
                       filter: Optional[str] = None, limit: Optional[int] = None,
                       page: Optional[int] = None, order: Optional[str] = None) -> Any:
        """
        Browse authors using various filtering and pagination options.
        
        Args:
            include: Optional list of fields to include in the response.
            fields: Optional list of specific fields to retrieve.
            filter: Optional string filter to apply to the results.
            limit: Optional integer specifying the maximum number of results to return.
            page: Optional integer indicating the page number.
            order: Optional string defining the order of results.
        
        Returns:
            Any data returned from the GET request to the authors endpoint.
        
        Tags:
            list, management, important
        """
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order
        }
        return self._execute_get_request("authors/", params)

    def read_author_by_id(self, id: str, include: Optional[List[str]] = None,
                          fields: Optional[List[str]] = None) -> Any:
        """
        Read an author from the database by their unique ID.
        
        Args:
            id: The unique identifier of the author.
            include: Optional list of related resources to include in the response.
            fields: Optional list of fields to retrieve from the author record.
        
        Returns:
            The author data as a JSON response or other arbitrary data type.
        
        Tags:
            read, author, data-access
        """
        params = {"include": include, "fields": fields}
        return self._execute_get_request(f"authors/{id}/", params)

    def read_author_by_slug(self, slug: str, include: Optional[List[str]] = None,
                            fields: Optional[List[str]] = None) -> Any:
        """
        Retrieve an author's information by their slug.
        
        Args:
            slug: The unique slug of the author to retrieve.
            include: Optional list of fields to include in the response.
            fields: Optional list of fields to request in the response.
        
        Returns:
            The result of the GET request to retrieve the author's information.
        
        Tags:
            fetch, author, management
        """
        params = {"include": include, "fields": fields}
        return self._execute_get_request(f"authors/slug/{slug}/", params)

    # --- Tags Tools ---
    def browse_tags(self, include: Optional[List[str]] = None, fields: Optional[List[str]] = None,
                    filter: Optional[str] = None, limit: Optional[int] = None,
                    page: Optional[int] = None, order: Optional[str] = None) -> Any:
        """
        Browse and retrieve tags based on specified parameters.
        
        Args:
            include: Optional list of tags or fields to include.
            fields: Optional list of specific fields to retrieve.
            filter: Optional filter expression to apply.
            limit: Optional limit on the number of tags to return.
            page: Optional page number for pagination.
            order: Optional ordering criteria for the returned tags.
        
        Returns:
            Response from the GET request to retrieve tags.
        
        Raises:
            ConnectionError: Raised if the connection to the server fails.
            TimeoutError: Raised if the request times out.
        
        Tags:
            browse, tags, management, important
        """
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order
        }
        return self._execute_get_request("tags/", params)

    def read_tag_by_id(self, id: str, include: Optional[List[str]] = None,
                       fields: Optional[List[str]] = None) -> Any:
        """
        Retrieves a tag's details by its unique identifier, optionally filtering by included and field sets.
        
        Args:
            id: str: The unique identifier of the tag to retrieve.
            include: Optional[List[str]]: Additional related resources or attributes to include in the response.
            fields: Optional[List[str]]: Specific fields to return in the response.
        
        Returns:
            Any: The retrieved tag object, with details as specified by the included and field parameters. The exact type depends on the server response.
        
        Raises:
            Exception: Depending on the backend, may raise connection, authentication, or data retrieval exceptions.
        
        Tags:
            read, tag, search, fetch, api, management
        """
        params = {"include": include, "fields": fields}
        return self._execute_get_request(f"tags/{id}/", params)

    def read_tag_by_slug(self, slug: str, include: Optional[List[str]] = None,
                         fields: Optional[List[str]] = None) -> Any:
        """
        Retrieve tag information identified by a unique slug, with optional inclusion of related data and selective fields.
        
        Args:
            slug: str: The unique slug identifier of the tag to retrieve.
            include: Optional[List[str]]: A list of related resource names to include in the response, or None to include none.
            fields: Optional[List[str]]: A list of specific fields to return for the tag, or None to return all fields.
        
        Returns:
            Any: The data corresponding to the requested tag, potentially including related resources and filtered fields, as returned by the GET request.
        
        Raises:
            RequestException: If the underlying GET request fails due to network issues, invalid slug, or server errors.
        
        Tags:
            read, retrieve, get
        """
        params = {"include": include, "fields": fields}
        return self._execute_get_request(f"tags/slug/{slug}/", params)

    # --- Pages Tools ---
    def browse_pages(self, include: Optional[List[str]] = None, fields: Optional[List[str]] = None,
                     filter: Optional[str] = None, limit: Optional[int] = None,
                     page: Optional[int] = None, order: Optional[str] = None,
                     formats: Optional[List[str]] = None) -> Any:
        """
        Retrieves a list of pages using optional filtering, pagination, and formatting parameters.
        
        Args:
            include: Optional list of related resources or entities to include in the response.
            fields: Optional list of specific fields to include for each page in the response.
            filter: Optional string to filter the pages based on certain criteria.
            limit: Optional integer specifying the maximum number of pages to return.
            page: Optional integer specifying the page number for pagination.
            order: Optional string defining the sorting order of the returned pages.
            formats: Optional list of formats that the pages should be returned in.
        
        Returns:
            The result of the GET request to the 'pages/' endpoint, typically a collection of pages matching the query parameters.
        
        Raises:
            RequestException: If the underlying GET request fails due to network issues, invalid parameters, or server errors.
        
        Tags:
            browse, list, management, important
        """
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order, "formats": formats
        }
        return self._execute_get_request("pages/", params)

    def read_page_by_id(self, id: str, include: Optional[List[str]] = None,
                        fields: Optional[List[str]] = None, formats: Optional[List[str]] = None) -> Any:
        """
        Read a page by ID, allowing for optional inclusion of additional data, specific fields, and formats.
        
        Args:
            id: The unique identifier of the page to be read.
            include: Optional list of related data to include in the response.
            fields: Optional list of fields to filter the response by.
            formats: Optional list of formats for the response data.
        
        Returns:
            The result of the GET request to read the page.
        
        Tags:
            read, page, data-retrieval
        """
        params = {"include": include, "fields": fields, "formats": formats}
        return self._execute_get_request(f"pages/{id}/", params)

    def read_page_by_slug(self, slug: str, include: Optional[List[str]] = None,
                          fields: Optional[List[str]] = None, formats: Optional[List[str]] = None) -> Any:
        """
        Retrieve a page's content and metadata by its slug identifier, optionally including related data, specific fields, and content formats.
        
        Args:
            slug: The unique slug string identifying the page to be retrieved.
            include: Optional list of related resource names to include in the response.
            fields: Optional list of specific fields of the page to return.
            formats: Optional list of content formats to retrieve for the page.
        
        Returns:
            The response from the GET request containing the page data, typically as a parsed JSON object or equivalent.
        
        Raises:
            RequestException: If the underlying GET request fails due to network issues, invalid slug, or server errors.
        
        Tags:
            read, get, page, slug, http-request
        """
        params = {"include": include, "fields": fields, "formats": formats}
        return self._execute_get_request(f"pages/slug/{slug}/", params)

    # --- Tiers Tool ---
    def browse_tiers(self, include: Optional[List[str]] = None, fields: Optional[List[str]] = None,
                     filter: Optional[str] = None, limit: Optional[int] = None,
                     page: Optional[int] = None, order: Optional[str] = None) -> Any:
        """
        Browse tiers based on optional filters and pagination.
        
        Args:
            include: List of items to include in the response, if applicable.
            fields: List of fields to retrieve from the tiers.
            filter: String filter to apply to the tiers.
            limit: Maximum number of tiers to return in the response.
            page: Page number for pagination.
            order: Ordering parameter for the tiers.
        
        Returns:
            Response from the tiers browsing request.
        
        Raises:
            Exception: Raised on any issue during the execution of the GET request.
        
        Tags:
            browse, pagination, filter, management, important
        """
        params = {
            "include": include, "fields": fields, "filter": filter,
            "limit": limit, "page": page, "order": order
        }
        return self._execute_get_request("tiers/", params)

    # --- Settings Tool ---
    def browse_settings(self) -> Any:
        """
        Fetches site settings by making a GET request to the settings endpoint.
        
        Args:
            None: This function does not accept any parameters.
        
        Returns:
            The result of the GET request to retrieve site settings.
        
        Tags:
            fetch, settings, management, important
        """
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
