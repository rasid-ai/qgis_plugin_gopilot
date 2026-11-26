# RASID Configuration
# Centralized configuration for API and app URLs

import os
from urllib.parse import urlsplit

# API Base URL (with /api/ suffix)
API_BASE_URL = "https://api.rasid.ai/api/"

# App Base URL (without trailing slash)
APP_BASE_URL = "https://app.rasid.ai"

# Public marketing site. Separate from APP_BASE_URL: the portal is behind a
# login, so pages meant to be read without signing in (About, Terms) live
# here. Used by Settings -> About & feedback.
WEBSITE_URL = "https://rasid.ai"

# API Host (for image loader and other direct API calls)
API_HOST = "https://api.rasid.ai"

# GoPilot LLM Base URL
GOPILOT_LLM_BASE_URL = "https://api.rasid.ai/api/llm/"

# Domains the plugin will send its API token to, matched on the host or any
# subdomain of it.
#
# This exists because GoPilot's answers link to files on a host that is NOT
# API_HOST: the observed download URL is
# ``https://app.gopilot.earth/api/llm/files/<id>/download/``. Those endpoints
# need the Authorization header, and the old rule — "authenticate only a URL
# starting with API_HOST, treat everything else as external" — stripped it,
# so every result download failed with
# ``401 Authentication credentials were not provided``.
#
# An ALLOWLIST rather than a blocklist of storage providers. The token must
# reach our own API and must not reach anyone else's: a presigned S3 link in
# an answer is attacker-influenced data, and "send auth unless the URL looks
# like S3" fails open the moment storage moves or a link is crafted to look
# unlike S3.
TRUSTED_API_DOMAINS = ("rasid.ai", "gopilot.earth")

# Request timeout in seconds
REQUEST_TIMEOUT = 15

# Debug mode (keep False for released/deployed builds — when True, debug_print
# logs LLM message content, session IDs, URLs and request payloads to the log)
DEBUG = False


_VERSION_CACHE = []


def plugin_version():
    """The version in ``metadata.txt``, which is the one QGIS installed.

    Read from the manifest rather than written out a second time in Python.
    The feedback dialog used to send a hardcoded ``"2.0.0"`` while the
    manifest said ``0.2.5``, so every report filed from the plugin named a
    version that has never existed and could not be matched to a build.
    """
    if _VERSION_CACHE:
        return _VERSION_CACHE[0]
    version = "unknown"
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "metadata.txt")
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("version="):
                    version = line.split("=", 1)[1].strip() or "unknown"
                    break
    except OSError:
        pass
    _VERSION_CACHE.append(version)
    return version


def is_trusted_api_url(url):
    """Should the plugin send its API token to ``url``?

    True only for our own hosts, so a result link pointing anywhere else is
    fetched anonymously rather than with the user's credentials attached.

    Compares the parsed HOST against :data:`TRUSTED_API_DOMAINS`, not a string
    prefix. A prefix test gets two things wrong that matter here:

    * ``url.startswith(API_HOST + "/")`` rejects ``https://api.rasid.ai`` with
      no path and every other host we own, which is the bug this replaces;
    * a bare ``in`` or ``startswith`` on the domain would accept
      ``https://rasid.ai.attacker.example/`` — matching on the host with a
      leading dot cannot.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False        # not a URL we can reason about; assume untrusted
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain)
               for domain in TRUSTED_API_DOMAINS)
