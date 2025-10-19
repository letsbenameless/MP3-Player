import os
import urllib.parse
import requests
import threading
from flask import Flask, request, redirect, url_for

# Updated import path — now uses the DB version
from backend.services.spotify_library_export import export_full_library

app = Flask(__name__)

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8888/callback"
SCOPE = "user-library-read playlist-read-private playlist-read-collaborative"

# ──────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────

@app.route("/login")
def login():
    """Redirect user to Spotify authorization page."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": SCOPE,
        "redirect_uri": REDIRECT_URI,
        "show_dialog": "true",  # always re-prompt for login during testing
    }
    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)
    return redirect(auth_url)


@app.route("/callback")
def callback():
    """Spotify redirects here after user authorizes the app."""
    code = request.args.get("code")
    if not code:
        return "❌ No authorization code returned.", 400

    # ─── Exchange authorization code for access token ─────────
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=10,
    )

    if not resp.ok:
        print("Spotify token exchange failed:", resp.status_code, resp.text)
        return f"❌ Token exchange failed:<br><pre>{resp.text}</pre>", 400

    access_token = resp.json().get("access_token")
    if not access_token:
        return "❌ Spotify did not return an access token.", 400

    print("✅ Spotify access token retrieved successfully.")

    # ─── Run the export in a background thread ─────────────────
    thread = threading.Thread(
        target=export_full_library,  # new DB export version
        args=(access_token,),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("exporting"))


@app.route("/exporting")
def exporting():
    """Simple confirmation page shown immediately after login."""
    return """
    <html>
    <head><title>Exporting your Spotify library...</title></head>
    <body style="font-family:sans-serif; text-align:center; margin-top:4em;">
      <h2>🎵 Your Spotify library is being exported!</h2>
      <p>This process runs in the background and writes directly to your database.</p>
      <p>You can safely close this window — check your terminal for live progress.</p>
    </body>
    </html>
    """


if __name__ == "__main__":
    app.run(port=8888, debug=True)
