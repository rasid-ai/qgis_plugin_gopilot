# API session / token management
import os
import tempfile
import requests
from .config import API_BASE_URL, API_HOST, REQUEST_TIMEOUT, GOPILOT_LLM_BASE_URL, is_trusted_api_url
from .gopilot_client import GoPilotClient
from .logger import debug_print

class RasidClient:
    """
    RASID API Client with API key authentication.

    Authentication:
    - Set via set_api_key() or load from QSettings
    - No login required
    - No CSRF tokens needed
    - Secure (no password storage)
    """

    def __init__(self):
        self.session = requests.Session()
        self.base_url = API_BASE_URL
        self.api_key = None  # Will be set via set_api_key() or load_api_key()
        self.gopilot = None  # GoPilot client, initialized after setting API key

    # ============================================================================
    # API KEY AUTHENTICATION (New, Recommended)
    # ============================================================================

    def set_api_key(self, api_key):
        """
        Set the API key for authentication.

        Args:
            api_key: Your RASID API key (format: rsd_live_... or rsd_test_...)
        """
        if not api_key:
            self.api_key = None
            if "Authorization" in self.session.headers:
                del self.session.headers["Authorization"]
            if "X-API-Key" in self.session.headers:
                del self.session.headers["X-API-Key"]
            return

        self.api_key = api_key.strip()
        # Set Authorization header (preferred method)
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"

        # Initialize GoPilot client with the API key
        try:
            self.gopilot = GoPilotClient(GOPILOT_LLM_BASE_URL, self.api_key)
        except Exception as e:
            debug_print(f"[RASID] Failed to initialize GoPilot client: {e}")
            self.gopilot = None

    def save_api_key(self, api_key=None):
        """
        Save API key to OS secure credential storage.

        Uses the keyring library which automatically selects the appropriate
        secure backend for the current OS (Windows Credential Manager, macOS
        Keychain, Linux Secret Service, etc.). All backends provide encryption.

        This is more secure than QSettings which stores in plaintext.

        Args:
            api_key: Optional API key to save. If None, saves current self.api_key
        """
        try:
            import keyring
        except ImportError:
            # Keyring not installed yet, skip saving
            return

        SERVICE_NAME = "rasid_plugin"
        USERNAME = "api_key"

        key_to_save = api_key or self.api_key
        if key_to_save:
            # Save to secure OS credential storage
            keyring.set_password(SERVICE_NAME, USERNAME, key_to_save)
            self.set_api_key(key_to_save)
        else:
            # Delete from secure storage
            try:
                keyring.delete_password(SERVICE_NAME, USERNAME)
            except keyring.errors.PasswordDeleteError:
                pass  # Key didn't exist, that's fine

    def load_api_key(self):
        """
        Load API key from OS secure credential storage.

        Automatically uses the appropriate backend for your OS.

        Returns:
            bool: True if API key was loaded, False otherwise
        """
        try:
            import keyring
        except ImportError:
            # Keyring not installed yet
            return False

        SERVICE_NAME = "rasid_plugin"
        USERNAME = "api_key"

        try:
            api_key = keyring.get_password(SERVICE_NAME, USERNAME)
            if api_key:
                self.set_api_key(api_key)
                return True
        except Exception as exc:
            # Keyring not available or no key stored — treat as "no key".
            debug_print(f"[RasidClient] Could not read API key from keyring: {exc}")

        return False

    def clear_api_key(self):
        """Clear API key from session and OS secure credential storage."""
        try:
            import keyring
        except ImportError:
            # Keyring not installed, just clear session
            self.set_api_key(None)
            return

        SERVICE_NAME = "rasid_plugin"
        USERNAME = "api_key"

        # Clear from session
        self.set_api_key(None)

        # Clear GoPilot client
        self.gopilot = None

        # Clear from secure storage
        try:
            keyring.delete_password(SERVICE_NAME, USERNAME)
        except keyring.errors.PasswordDeleteError:
            pass  # Key didn't exist, that's fine

    def has_api_key(self):
        """Check if an API key is configured."""
        return bool(self.api_key)


    # ============================================================================
    # AUTHENTICATION STATUS
    # ============================================================================

    def is_authenticated(self):
        """
        Test if current session/API key is valid.

        Returns:
            bool: True if authenticated (via API key or session), False otherwise
        """
        try:
            self.get_profile()
            return True
        except Exception:
            return False

    def get_auth_method(self):
        """
        Get the current authentication method.

        Returns:
            str: 'api_key' or 'none'
        """
        if self.api_key:
            return 'api_key'
        else:
            return 'none'

    def logout(self):
        """
        Logout and clear API key.
        """
        self.clear_api_key()

    # ============================================================================
    # API METHODS
    # ============================================================================

    def get_profile(self):
        """Get user profile information."""
        response = self.session.get(
            self.base_url + "accounts/profile/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_gotokens(self):
        """Get the GoToken wallet.

        A SEPARATE call from :meth:`get_profile` on purpose: the balance is not
        in the profile response. It used to be (the plugin read
        ``profile["balance"]["amount"]`` and showed it as euros), but the
        product moved to GoTokens and that field went away — leaving the plugin
        reporting a confident "0" for everyone, because a missing key defaulted
        to zero.

        Returns the watcher's shape::

            {
              "wallet": {"balance": "2404153.0000",       # decimal AS A STRING
                         "allowance_balance": "...",
                         "topup_balance": "...",
                         "monthly_allowance": "...",
                         "is_overdrawn": false, ...} | null,
              "can_send": true,
              "rate_configured": true,
            }

        ``wallet`` is nullable — a user with no plan has no wallet at all,
        which is NOT the same thing as a zero balance.
        """
        response = self.session.get(
            self.base_url + "billing/gotokens/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_user_projects(self, hidden=False):
        """Get list of user's projects."""
        params = {"hidden": str(hidden).lower()}
        response = self.session.get(
            self.base_url + "projects/", params=params, timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 200:
            return response.json()
        else:
            detail = response.json().get("detail", "Failed to fetch projects")
            raise Exception(detail)

    def get_solutions(self):
        """Get list of available solutions."""
        response = self.session.get(
            self.base_url + "solutions/", timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()

    def get_processes(self, project_slug):
        """Get list of processes for a project."""
        response = self.session.get(
            self.base_url + "processes/",
            params={"project": project_slug},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def create_project(self, solution_slug, title, tags=None):
        """
        Create a new project.

        Args:
            solution_slug: Slug of the solution to use
            title: Title for the new project
            tags: Optional list of tag IDs
        """
        payload = {"solution_slug": solution_slug, "title": title}
        if tags:
            payload["tags"] = tags
        response = self.session.post(
            self.base_url + "projects-create/",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 201:
            return response.json()
        else:
            detail = response.json() if response.content else {"detail": "Failed to create project"}
            if isinstance(detail, dict):
                detail = detail.get("detail", str(detail))
            raise Exception(str(detail))

    def get_process_config(self, project_slug):
        """Get process configuration for a project."""
        response = self.session.get(
            self.base_url + f"projects/{project_slug}/process-config/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def search_catalogue(self, payload):
        """
        Search Sentinel catalogue.

        Args:
            payload: Dict with keys:
                - bbox: string "[min_lon, min_lat, max_lon, max_lat]" or list
                - sentinel_start_date: string "YYYY-MM-DD"
                - sentinel_end_date: string "YYYY-MM-DD"
                - sentinel_data_collection: string (sentinel-2-l2a, sentinel-2-l1c, sentinel-1-grd)
                - sentinel_sort_by: string (optional: "---", "leastcc")
        """
        import json

        # Convert bbox list to JSON string if needed
        if isinstance(payload.get("bbox"), list):
            payload = payload.copy()
            payload["bbox"] = json.dumps(payload["bbox"])

        # Build workspace URL from base URL (remove /api/ suffix)
        url = self.base_url + "sentinel2-catalogue/"

        response = self.session.post(
            url,
            data=payload,  # Use form data, not JSON
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def create_process(self, project_slug, payload, files=None):
        """
        Create a new process under a project.

        Args:
            project_slug: Project slug
            payload: Dict with process creation data (see CreateProcessInputSerializer)
            files: Optional dict of files (e.g., {'upload_raster': file_handle})
        """
        # When sending files, we must use multipart/form-data (data=, not json=)
        # Temporarily remove Content-Type header to let requests set it automatically
        headers = {}
        if files:
            # Let requests set Content-Type with proper boundary
            if "Content-Type" in self.session.headers:
                headers["Content-Type"] = None

        response = self.session.post(
            self.base_url + f"projects/{project_slug}/processes/",
            data=payload,
            files=files,
            headers=headers or None,
            timeout=120 if files else REQUEST_TIMEOUT,
        )
        if response.status_code == 201:
            return response.json()
        else:
            detail = response.json() if response.content else {"detail": "Failed"}
            if isinstance(detail, dict):
                detail = detail.get("detail", str(detail))
            raise Exception(str(detail))

    def hide_process(self, project_slug, process_id):
        """Hide a process (soft delete)."""
        response = self.session.post(
            self.base_url + f"projects/{project_slug}/processes/{process_id}/hide/",
            json={},  # Send empty JSON body
            timeout=REQUEST_TIMEOUT,
        )
        # FIXED: Handle both 200 and 204 responses without trying to parse empty body
        if response.status_code in (200, 204):
            return True
        else:
            detail = "Failed to hide process"
            if response.content:
                try:
                    detail = response.json().get("detail", str(response.json()))
                except Exception:
                    detail = response.text
            raise Exception(detail)

    def hide_project(self, project_slug):
        """Hide a project (soft delete)."""
        response = self.session.post(
            self.base_url + f"projects/{project_slug}/hide/",
            json={},  # Send empty JSON body
            timeout=REQUEST_TIMEOUT,
        )
        # FIXED: Handle both 200 and 204 responses without trying to parse empty body
        if response.status_code in (200, 204):
            return True
        else:
            detail = "Failed to hide project"
            if response.content:
                try:
                    detail = response.json().get("detail", str(response.json()))
                except Exception:
                    detail = response.text
            raise Exception(detail)

    def get_process_detail(self, process_id):
        """Get detailed information about a process."""
        response = self.session.get(
            self.base_url + "process/detail/",
            params={"id": process_id},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def submit_feedback(self, feedback_data):
        """
        Submit user feedback to the API.

        Args:
            feedback_data: Dict with keys:
                - message: string (required) - The feedback message
                - rating: integer (optional) - Rating 0-5, default 0
                - process: integer (optional) - Process ID to link feedback to
                - feedback_infos: dict (optional) - Additional metadata

        Returns:
            dict: Created feedback object with id, system_registration_date, etc.
        """
        response = self.session.post(
            self.base_url + "accounts/feedback/",
            json=feedback_data,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 201:
            return response.json()
        else:
            # Try to parse error response
            detail = "Failed to submit feedback"
            if response.content:
                try:
                    error_data = response.json()
                    if isinstance(error_data, dict):
                        detail = error_data.get("detail", str(error_data))
                    else:
                        detail = str(error_data)
                except Exception:
                    # If JSON parsing fails, use raw text
                    detail = f"Server error ({response.status_code}): {response.text[:200]}"
            raise Exception(str(detail))

    def download_file(self, url, dest_dir=None):
        """
        Download a file from the API and save it locally with path traversal protection.

        This function implements security measures to prevent path traversal attacks:
        - Extracts only the basename from URL to strip directory components
        - Blocks dangerous filenames (., .., or containing path separators)
        - Validates that the final resolved path stays within the destination directory
        - Uses os.path.realpath() to resolve any remaining path traversal attempts

        These protections prevent malicious URLs from writing files outside the intended
        directory, which could lead to:
        - System file corruption
        - Arbitrary code execution via startup folders
        - Overwriting configuration files
        - Privilege escalation attacks

        Args:
            url: URL to download from (absolute or relative to BASE_URL)
            dest_dir: Destination directory (defaults to temp/rasid_downloads)

        Returns:
            str: Local file path where the file was saved

        Raises:
            Exception: If URL is invalid, download fails, or path traversal detected
        """
        if not url:
            raise Exception("No file URL provided")
        if url.startswith("/"):
            url = API_HOST + url

        # Send the token to our own hosts, and to nobody else.
        #
        # This was a blocklist: "external" meant an S3-looking URL, or ANY
        # https URL not starting with API_HOST — and GoPilot serves result
        # files from `app.gopilot.earth`, so every download of a chat result
        # went out unauthenticated and came back
        # `401 Authentication credentials were not provided`.
        #
        # Inverted to an allowlist (config.is_trusted_api_url), which fixes
        # that without the opposite failure: a presigned storage link in an
        # answer is attacker-influenced, and it must not receive the user's
        # credentials just because it does not look like S3.
        if is_trusted_api_url(url):
            response = self.session.get(url, timeout=60, stream=True)
        else:
            response = requests.get(url, timeout=60, stream=True)

        response.raise_for_status()

        # SECURITY: Extract filename from URL
        filename = url.rsplit("/", 1)[-1].split("?")[0] or "download"

        # SECURITY: Use basename to strip any directory components (../, ./, etc.)
        filename = os.path.basename(filename)

        # SECURITY: Block dangerous filenames that could still cause issues
        if not filename or filename in (".", "..") or "/" in filename or "\\" in filename:
            filename = "download"

        # SECURITY: Additional check - block filenames starting with dots (hidden files)
        # that might overwrite config files
        if filename.startswith("."):
            filename = "download" + filename

        if dest_dir is None:
            dest_dir = os.path.join(tempfile.gettempdir(), "rasid_downloads")
        os.makedirs(dest_dir, exist_ok=True)

        filepath = os.path.join(dest_dir, filename)

        # SECURITY: Final validation - ensure resolved path is within destination
        # This catches any edge cases or OS-specific path traversal methods
        real_dest = os.path.realpath(dest_dir)
        real_path = os.path.realpath(filepath)

        if not real_path.startswith(real_dest + os.sep) and real_path != real_dest:
            raise Exception(
                f"Security: Path traversal attempt detected. "
                f"File would be written outside download directory."
            )

        # Safe to write file
        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return filepath
