# Footwear Check

A local school footwear monitoring app using `footwear_model_package/best.pt`.

## Start

1. Double-click **start.bat** and keep its terminal open.
2. Open **http://127.0.0.1:5000** in your browser.
3. Create your first administrator account; there is no default password.
4. Open **Live detection** and select **Start camera**. Model loading can take a moment.
5. Stop the camera when finished. Press Ctrl+C in the server terminal to shut down.

If dependencies are missing, run `powershell -ExecutionPolicy Bypass -File .\setup.ps1` from this folder. It downloads Python and dependencies into `.runtime` without changing system Python. Internet is required for installation. Alternatively, use Python 3.11/3.12 in a virtual environment, install `requirements.txt`, and run `app.py`.

## Features

- First-run admin setup; hashed passwords; CSRF protection; login throttling; eight-hour sessions; password changes.
- Admin and staff accounts. Admins create accounts and manage settings; staff can detect, review, and export history.
- Dashboard with today's event counts and recent results.
- Webcam preview with bounding boxes, confidence, and processing FPS.
- Five matching frames required to record a result. Clear the camera of footwear for two seconds before the next person; a minimum 12-second interval also applies.
- Low confidence and conflicting accepted classes show **Uncertain**. No boxes shows **No footwear detected**. Neither state creates a valid/invalid record.
- Filtered, paginated history; CSV export; manual staff reviews that preserve model results.
- Configurable camera index and confidence; optional snapshots, off by default. Snapshots expire during app use; record metadata remains.

## Camera

Use one person at a time with shoes fully visible in even lighting. Tilt or reposition the laptop so the webcam sees footwear. Camera 0 normally selects the built-in camera; try 1 in Settings for another camera. Restart the camera after settings changes. Close Zoom, Teams, or other camera apps if the webcam cannot open. Check Windows Settings > Privacy & security > Camera > Let desktop apps access your camera. The Python server accesses the webcam directly, so browser camera permission is not required.

The camera stays on while navigating the app. Stop it when finished. Signing out from any session also stops the shared camera. Use one monitoring session on this laptop.

Counts represent events, not unique students. The model cannot identify students or determine which person owns each detected shoe. Staff must verify results before taking action. Actual accuracy and speed require hands-on testing with representative school footwear and lighting. The supplied model is used as-is; it is not retrained.

## Data

Accounts/settings/history: `instance/footwear.sqlite`. Images: `instance/snapshots/`. Session secret: `instance/secret.key`. Back up the whole instance folder while the app is stopped. Do not share it or commit it to source control. Expired image deletion is permanent. OneDrive may sync this project's local records depending on your settings; move the project outside OneDrive to avoid that.

The server listens only on 127.0.0.1. Keep it local; network deployment needs additional configuration. Times use the laptop's local timezone.

## Tests

Run `.runtime\python\python.exe -m unittest discover -s tests -v`. These verify authentication, CSRF, roles, settings, retention, filtering/export, review, camera API routing, and duplicate suppression without opening the webcam.
