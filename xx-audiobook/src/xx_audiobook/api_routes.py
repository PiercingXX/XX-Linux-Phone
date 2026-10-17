"""Client route table: every server path the Linux client speaks.

Paths are relative to the API base ({origin}/audiobook/). Estate login,
refresh and hub discovery are NOT part of the audiobook server contract
(openapi.json) and are listed separately. The contract test pins this table
against openapi.json: each entry must exist exactly once with the method.
"""

from __future__ import annotations

# (method, path-template) spoken against {origin}/audiobook/.
CLIENT_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/me"),
    ("GET", "/api/status"),
    ("GET", "/api/items"),
    ("GET", "/api/items/{item_id}"),
    ("GET", "/api/items/{item_id}/cover"),
    ("GET", "/api/items/{item_id}/manifest"),
    ("GET", "/api/items/{item_id}/files/{file_id}"),
    ("HEAD", "/api/items/{item_id}/files/{file_id}"),
    ("GET", "/api/library/facets"),
    ("GET", "/api/search"),
    ("GET", "/api/series"),
    ("GET", "/api/series/{series_id}"),
    ("GET", "/api/authors"),
    ("GET", "/api/authors/{author_id}"),
    ("GET", "/api/narrators"),
    ("GET", "/api/narrators/{narrator_id}"),
    ("GET", "/api/collections"),
    ("GET", "/api/collections/{collection_id}"),
    ("POST", "/api/items/{item_id}/play"),
    ("POST", "/api/sessions/{session_id}/sync"),
    ("POST", "/api/sessions/{session_id}/close"),
    ("GET", "/api/me/continue"),
    ("GET", "/api/me/home"),
    ("GET", "/api/me/progress"),
    ("GET", "/api/me/progress/{item_id}"),
    ("PUT", "/api/me/progress/{item_id}"),
    ("DELETE", "/api/me/progress/{item_id}"),
    ("POST", "/api/me/progress/{item_id}/finish"),
    ("POST", "/api/me/progress/{item_id}/unfinish"),
    ("POST", "/api/me/progress/batch"),
    ("GET", "/api/me/stats"),
    ("GET", "/api/me/sessions"),
    ("DELETE", "/api/me/sessions/{session_id}"),
    ("GET", "/api/me/settings"),
    ("PUT", "/api/me/settings"),
    ("GET", "/api/me/playlists"),
    ("POST", "/api/me/playlists"),
    ("GET", "/api/me/playlists/{playlist_id}"),
    ("PATCH", "/api/me/playlists/{playlist_id}"),
    ("DELETE", "/api/me/playlists/{playlist_id}"),
    ("POST", "/api/me/playlists/{playlist_id}/items"),
    ("PUT", "/api/me/playlists/{playlist_id}/items"),
    ("DELETE", "/api/me/playlists/{playlist_id}/items/{item_id}"),
    ("GET", "/api/me/bookmarks"),
    ("POST", "/api/me/bookmarks"),
    ("GET", "/api/me/bookmarks/{bookmark_id}"),
    ("PATCH", "/api/me/bookmarks/{bookmark_id}"),
    ("DELETE", "/api/me/bookmarks/{bookmark_id}"),
    ("PUT", "/api/items/{item_id}/review"),
    ("DELETE", "/api/items/{item_id}/review"),
    ("GET", "/api/items/{item_id}/reviews"),
    ("GET", "/api/reviews/recent"),
    ("GET", "/api/locations"),
    ("GET", "/api/cast/targets"),
    ("POST", "/api/cast/play"),
    ("POST", "/api/cast/transport"),
)

# Estate fabric endpoints (NOT in openapi.json; served by the estate door).
ESTATE_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("GET", "/hub/discovery"),
)
