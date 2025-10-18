import os, urllib.parse, requests, threading
from flask import Flask, request, redirect, url_for
from backend.services.spotify_library_export import export_full_library

app = Flask(__name__)
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8888/callback"
SCOPE = "user-library-read playlist-read-private playlist-read-collaborative"


@app.route("/login")
def login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": SCOPE,
        "redirect_uri": REDIRECT_URI,
        "show_dialog": "true",  # force re-login for testing
    }
    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)
    return redirect(auth_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "❌ No authorization code returned.", 400

    # Exchange code for token
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    if not resp.ok:
        print("Spotify token exchange failed:", resp.status_code, resp.text)
        return f"❌ Token exchange failed:<br><pre>{resp.text}</pre>", 400

    access_token = resp.json().get("access_token")
    if not access_token:
        return "❌ Spotify did not return an access token.", 400

    print("✅ Access Token:", access_token)

    # ─── Run export in background thread ──────────────────────────────
    thread = threading.Thread(
        target=export_full_library,
        args=(access_token, "./exports"),
        daemon=True
    )
    thread.start()

    # ─── Redirect user immediately ───────────────────────────────────
    return redirect(url_for("exporting"))


@app.route("/exporting")
def exporting():
    return """
    <html>
    <head><title>Exporting your library...</title></head>
    <body style="font-family:sans-serif; text-align:center; margin-top:4em;">
      <h2>🎵 Your Spotify library is exporting!</h2>
      <p>This may take a few minutes depending on how many playlists you have.</p>
      <p>Check your terminal for progress logs.</p>
      <p>You can safely close this window.</p>
    </body>
    </html>
    """


if __name__ == "__main__":
    app.run(port=8888, debug=True)
