# -*- coding: utf-8 -*-
"""
GoPilot LLM Client
Handles communication with the GoPilot AI chat backend
"""

import requests
from typing import Optional, List, Dict, Any
from .config import REQUEST_TIMEOUT
from .logger import debug_print


class GoPilotClient:
    """Client for GoPilot LLM chat API"""

    def __init__(self, base_url: str, token: str):
        """
        Initialize GoPilot client.

        Args:
            base_url: Base URL of the API (e.g., "https://api.rasid.ai/api/llm/")
            token: Authentication token
        """
        self.base_url = base_url.rstrip('/') + '/'
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {token}'
        })

    # ========== SESSION MANAGEMENT ==========

    def create_session(
        self,
        title: str = "New Chat",
        content: str = "",
        input_metadata: Optional[Dict[str, Any]] = None,
        files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new LLM chat session.

        Args:
            title: Session title (default: "New Chat")

        Returns:
            Session object with id, title, order_index, etc.
        """
        import json

        data = {"title": title, "content": content}
        if input_metadata:
            data["input_metadata"] = json.dumps(input_metadata)

        files_to_upload = None
        if files:
            files_to_upload = [
                ("files", open(file_path, "rb"))
                for file_path in files
            ]

        try:
            if files_to_upload:
                response = self.session.post(
                    self.base_url + "sessions/",
                    data=data,
                    files=files_to_upload,
                    timeout=REQUEST_TIMEOUT,
                )
            else:
                payload = {"title": title, "content": content}
                if input_metadata:
                    payload["input_metadata"] = input_metadata
                response = self.session.post(
                    self.base_url + "sessions/",
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )

            response.raise_for_status()
            return response.json()
        finally:
            if files_to_upload:
                for _, file_handle in files_to_upload:
                    file_handle.close()

    def get_session_history(self) -> Dict[str, Any]:
        """
        Get all chat sessions for the authenticated user.

        Returns:
            Dict with 'count', 'next', 'previous', 'results' (list of sessions)
        """
        response = self.session.get(
            self.base_url + "sessions/history/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_session(self, session_id: int) -> Dict[str, Any]:
        """
        Get a specific session by ID.

        Args:
            session_id: Session ID

        Returns:
            Session object with full details
        """
        response = self.session.get(
            self.base_url + f"sessions/{session_id}/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def delete_session(self, session_id: int) -> None:
        """
        Delete a chat session.

        Args:
            session_id: Session ID to delete
        """
        response = self.session.delete(
            self.base_url + f"sessions/{session_id}/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

    # ========== MESSAGING ==========

    def send_message(
        self,
        session_id: int,
        content: str,
        input_metadata: Optional[Dict[str, Any]] = None,
        files: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Send a message to the LLM in a session.

        Args:
            session_id: Session ID
            content: Message text
            input_metadata: Optional metadata dict with 'type', 'drawing_coordinates', etc.
            files: Optional list of file paths to upload

        Returns:
            Dict with 'user_message', 'task', 'message'
        """
        import json

        data = {
            'content': content,
        }

        if input_metadata:
            # Serialize input_metadata as JSON string for multipart/form-data
            data['input_metadata'] = json.dumps(input_metadata)

        files_to_upload = None
        if files:
            files_to_upload = [
                ('files', open(file_path, 'rb'))
                for file_path in files
            ]

        try:
            # Debug logging
            debug_print(f"[GoPilot] Sending message to session {session_id}")
            debug_print(
                f"[GoPilot] URL: {self.base_url}sessions/{session_id}/send_message/")
            debug_print(f"[GoPilot] Data: {data}")

            response = self.session.post(
                self.base_url + f"sessions/{session_id}/send_message/",
                data=data,
                files=files_to_upload,
                timeout=REQUEST_TIMEOUT,
            )

            debug_print(f"[GoPilot] Response status: {response.status_code}")
            debug_print(
                f"[GoPilot] Response content type: {response.headers.get('content-type')}")
            debug_print(f"[GoPilot] Response text: {response.text[:500]}")

            # Check for error status before parsing
            if response.status_code >= 400:
                error_detail = f"HTTP {response.status_code} Error"
                try:
                    error_json = response.json()
                    error_detail = f"{error_detail}: {error_json}"
                except ValueError:
                    error_detail = f"{error_detail}: {response.text[:200]}"
                debug_print(f"[GoPilot] Server error: {error_detail}")
                raise Exception(error_detail)

            response.raise_for_status()

            # Parse JSON response
            result = response.json()
            debug_print(f"[GoPilot] Parsed JSON type: {type(result)}")
            return result
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP {e.response.status_code} Error"
            try:
                error_json = e.response.json()
                error_msg = f"{error_msg}: {error_json}"
            except ValueError:
                error_msg = f"{error_msg}: {e.response.text[:200]}"
            debug_print(f"[GoPilot] HTTP Error in send_message: {error_msg}")
            raise Exception(error_msg)
        except Exception as e:
            debug_print(f"[GoPilot] Error in send_message: {e}")
            raise
        finally:
            # Close file handles
            if files_to_upload:
                for _, file_handle in files_to_upload:
                    file_handle.close()

    def get_messages(self, session_id: int) -> List[Dict[str, Any]]:
        """
        Get all messages for a session.

        Args:
            session_id: Session ID

        Returns:
            List of message objects (both user and LLM messages)
        """
        response = self.session.get(
            self.base_url + f"sessions/{session_id}/messages/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """
        Check the status of an LLM processing task.

        Args:
            task_id: Task ID returned from send_message

        Returns:
            Dict with 'task_id', 'status', 'progress', 'llm_message', etc.
        """
        response = self.session.get(
            self.base_url + f"sessions/tasks/{task_id}/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    # ========== FILES ==========

    def get_user_files(self) -> List[Dict[str, Any]]:
        """
        Get all files (uploaded or LLM-generated) for the current user.

        Returns:
            List of file objects
        """
        response = self.session.get(
            self.base_url + "files/user_files/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_file(self, file_id: int) -> Dict[str, Any]:
        """
        Get a specific file by ID.

        Args:
            file_id: File ID

        Returns:
            File object with metadata
        """
        response = self.session.get(
            self.base_url + f"files/{file_id}/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def delete_file(self, file_id: int) -> Dict[str, Any]:
        """
        Delete a file.

        Args:
            file_id: File ID to delete

        Returns:
            Success message
        """
        response = self.session.delete(
            self.base_url + f"files/{file_id}/delete_file/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def check_file_processing_status(self, file_id: int) -> Dict[str, Any]:
        """
        Check processing status of a file (useful for TIFF files being tiled).

        Args:
            file_id: File ID

        Returns:
            Dict with 'status', 'error', 'visualization_url', 'tiles_situation', etc.
        """
        response = self.session.get(
            self.base_url + f"files/{file_id}/check_processing_status/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def retry_file_processing(self, file_id: int) -> Dict[str, Any]:
        """
        Retry processing a failed TIFF file.

        Args:
            file_id: File ID to retry

        Returns:
            Dict with 'message' and 'worker_file_task_id'
        """
        response = self.session.post(
            self.base_url + f"files/{file_id}/retry_processing/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    # ========== PROMPT SUGGESTIONS ==========

    def get_prompt_suggestions(
        self, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Starter prompts for an empty chat, as configured in the admin.

        The same public endpoint the SaaS and its anonymous try-page use; the
        server narrows the list by audience and picks at random, so two calls
        return different handfuls. That is why the plugin draws ONCE per chat
        and caches the result (see ``gopilot_page``) rather than refetching —
        otherwise the cards would reshuffle under the user's cursor.

        Each row is ``{id, title, prompt_text, description, icon, category}``.
        ``title`` is what the card shows and ``prompt_text`` what gets sent:
        deliberately separate, so a short label can stand for a long
        instruction.
        """
        params = {"limit": limit} if limit else None
        response = self.session.get(
            self.base_url + "prompt-suggestions/",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        # DRF may or may not paginate this one depending on deployment, so
        # accept both a bare list and a {results: [...]} envelope rather than
        # crashing the empty state on a config difference.
        if isinstance(payload, dict):
            payload = payload.get("results", [])
        return payload if isinstance(payload, list) else []

    # ========== TRASH, RENAME, LIMITS ==========

    def hide_message(self, message_id: int) -> Dict[str, Any]:
        """Move one exchange to the trash.

        The backend takes the whole TURN — a prompt and its reply travel
        together — so this is called with either id and both rows move. The
        response reports which ones actually did, in ``trashed_ids``.

        Hidden, not deleted: the SaaS exposes restore and permanent-delete on
        the same rows, so this is reversible server-side even though the plugin
        offers no UI for it yet.
        """
        response = self.session.post(
            self.base_url + f"messages/{message_id}/hide/",
            json={},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def update_session(self, session_id: int, title: Optional[str] = None,
                       is_active: Optional[bool] = None) -> Dict[str, Any]:
        """Rename a chat, or set its active flag.

        A PATCH, so only the fields passed are sent — sending ``title=None``
        would otherwise blank a title the caller never meant to touch.
        """
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if is_active is not None:
            payload["is_active"] = is_active
        response = self.session.patch(
            self.base_url + f"sessions/{session_id}/",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def get_upload_limits(self) -> Dict[str, Any]:
        """What may be uploaded, and how large each kind may be.

        ``{accept, extensions, kinds: {kind: {extensions, max_bytes,
        mappable}}}``. The server is the only place this list is correct — the
        plugin used to send whatever the user picked and let the upload fail
        with whatever message the backend chose.
        """
        response = self.session.get(
            self.base_url + "upload-limits/",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
