"""The web app manifest: lets a phone or browser keep OctaPiHub like an app (home screen icon)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

MANIFEST: dict[str, object] = {
    "name": "OctaPiHub",
    "short_name": "OctaPiHub",
    "description": "Würfel, Licht, Musik und Smart Home im eigenen Netz",
    "lang": "de",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#f4f7f9",
    "theme_color": "#0c806c",
    "icons": [
        {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {
            "src": "/static/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
}


def register_pwa(app: FastAPI) -> None:
    """Serve ``/manifest.webmanifest`` (public, contains nothing private)."""

    @app.get("/manifest.webmanifest")
    def manifest() -> JSONResponse:
        return JSONResponse(MANIFEST, media_type="application/manifest+json")
