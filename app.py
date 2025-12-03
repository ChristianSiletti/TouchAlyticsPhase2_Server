# Imports
from datetime import datetime, timedelta
from collections import defaultdict
from threading import Lock

import numpy as np
import pandas as pd
from sklearn import model_selection, svm
from sklearn.metrics import accuracy_score, classification_report
from flask import Flask, request, jsonify
import pickle
import os

from auth import auth, mydb, SendEmail    # <-- MySQL connection, blueprint, and email sending function


app = Flask(__name__)
app.register_blueprint(auth)


class NeedMultipleUsers(Exception):
    """Raised when only one user is found with enough strokes."""
    pass


# ------------------------------ CONSTANTS --------------------------

MIN_STROKES = 90      # minimum number of touch strokes per user
TOUCH_ELEMENTS = 31   # elements per touch (kept for reference)
MODEL_FILE = "touch_model.pkl"  # File that contains the SVM model

# Alert thresholds
ALERT_TIME_WINDOW = 10  # seconds
MIN_FAILED_ATTEMPTS = 3
ALERT_EMAIL_COOLDOWN = 300  # seconds (5 minutes between alert emails per user)


# DB table name for swipe features
SWIPE_TABLE = "swipefeatures"

# Features required in the swipe authentication request AND
# the columns we read from the DB. This matches the swipefeatures table.
REQUIRED_FEATURES = [
    "userID",
    "strokeDuration",
    "midStrokeArea",
    "midStrokePress",
    "dirEndToEnd",
    "aveDir",
    "aveVelo",
    "pairwiseVeloPercent",
    "startX",
    "startY",
    "stopX",
    "stopY",
    "touchArea",
    "maxVelo",
    "minVelo",
    "accel",
    "decel",
    "trajLength",
    "curvature",
    "veloVariance",
    "angleChangeRate",
    "maxPress",
    "minPress",
    "initPress",
    "pressChangeRate",
    "pressVariance",
    "maxIdleTime",
    "straightnessRatio",
    "xDisplacement",
    "yDisplacement",
    "aveTouchArea",
]

# Alert thresholds
ALERT_TIME_WINDOW = 10  # seconds
MIN_FAILED_ATTEMPTS = 3

# Track failed authentication attempts per user
# Format: {user_id: [(timestamp1, matched_bool), (timestamp2, matched_bool), ...]}
user_attempts = defaultdict(list)
attempts_lock = Lock()

# Track if alert has been sent recently to avoid spam
# Format: {user_id: last_alert_timestamp}
last_alert_sent = {}


# -------------------------- FUNCTIONS ------------------------------


def measure_svm_accuracy(X, y):
    """Train/test split and print SVM accuracy + reports."""
    X_train, X_test, y_train, y_test = model_selection.train_test_split(
        X, y, test_size=0.2, random_state=2
    )

    h1 = svm.LinearSVC(C=1)  # SVM model
    h1.fit(X_train, y_train)

    y_pred = h1.predict(X_test)

    print(f"Prediction Accuracy: {accuracy_score(y_test, y_pred) * 100:.2f}%")
    print(f"\nCrosstab Table:\n{pd.crosstab(y_test, y_pred)}")
    print(f"\nClassification Report:\n{classification_report(y_test, y_pred)}")


def load_swipe_rows_from_db():
    """
    Load all strokes from the DB as a list of dicts.
    Uses a dictionary cursor so we can access by column name.
    """
    cursor = mydb.cursor(dictionary=True)

    # Select all the columns we know we need (REQUIRED_FEATURES)
    cols = ", ".join(REQUIRED_FEATURES)
    query = f"SELECT {cols} FROM {SWIPE_TABLE}"

    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()

    return rows


# ---- NEW: shared helper to compute "eligible" users ----------------
def get_eligible_users_and_strokes():
    """
    Returns:
        (eligible_user_ids, user_to_strokes_dict)

    - eligible_user_ids: list of userIDs with >= MIN_STROKES strokes
    - user_to_strokes_dict: {userID: [row_dict, ...]}
    """
    rows = load_swipe_rows_from_db()

    if not rows:
        return [], {}

    user_to_strokes = defaultdict(list)

    for row in rows:
        uid_raw = row["userID"]
        try:
            uid = int(uid_raw)
        except Exception:
            uid = str(uid_raw)
        user_to_strokes[uid].append(row)

    userIDlist = [
        uid for uid, strokes in user_to_strokes.items()
        if len(strokes) >= MIN_STROKES
    ]

    return userIDlist, user_to_strokes


def create_model():
    """
    Build/refresh the SVM model using swipe feature rows from the DB (swipefeatures).
    Applies MIN_STROKES per user and requires at least 2 such users.
    This function no longer uses users.csv or any prev/current user tracking.
    """

    # 1. Read data & compute eligible users
    userIDlist, user_to_strokes = get_eligible_users_and_strokes()

    if not user_to_strokes:
        print("No data in DB")
        raise ValueError("No data available in DB to train model.")

    if len(userIDlist) <= 1:
        print(f"Found {len(userIDlist)} user(s) with >= {MIN_STROKES} strokes")
        raise NeedMultipleUsers

    # 3. Build X, y
    # Feature order for X: all REQUIRED_FEATURES except "userID"
    feature_keys = [k for k in REQUIRED_FEATURES if k != "userID"]

    X_list = []
    y_list = []

    touchIDCount = 0

    for uid in userIDlist:
        for stroke in user_to_strokes[uid]:
            touchIDCount += 1

            # Label: userID for this stroke
            y_list.append(uid)

            # Features in REQUIRED_FEATURES order (excluding userID)
            row_vals = [stroke[key] for key in feature_keys]
            X_list.append(row_vals)

    if touchIDCount == 0:
        print("No valid strokes found for selected users")
        raise ValueError("No valid touch data to train model.")

    # Convert to numpy arrays
    X = np.array(X_list, dtype=float)
    y = np.array(y_list)

    # 4. Evaluate SVM accuracy on a hold-out set
    measure_svm_accuracy(X, y)

    # 5. Train full SVM model on all data
    h1 = svm.LinearSVC(C=1)
    h1.fit(X, y)

    # 6. Save the model
    with open(MODEL_FILE, 'wb') as f:
        pickle.dump(h1, f)

    print("Model trained and saved successfully")
    return h1


# -------------------------------------------------------------------

def check_failed_attempts(user_id, matched_bool: bool):
    """
    Track authentication attempts and trigger email alert if threshold is exceeded.
    Per-user tracking:
      - Each user_id has its own attempt history and alert cooldown.
    A security email is sent via SendEmail once the user reaches
    MIN_FAILED_ATTEMPTS failures within ALERT_TIME_WINDOW seconds,
    and at least ALERT_EMAIL_COOLDOWN seconds have passed since the last alert.
    """
    current_time = datetime.now()

    with attempts_lock:
        # Add current attempt for THIS user
        user_attempts[user_id].append((current_time, matched_bool))

        # Remove attempts older than the time window for THIS user
        cutoff_time = current_time - timedelta(seconds=ALERT_TIME_WINDOW)
        user_attempts[user_id] = [
            (timestamp, match) for timestamp, match in user_attempts[user_id]
            if timestamp > cutoff_time
        ]

        # Count failed attempts in the time window (False = failed)
        failed_attempts = [
            match for timestamp, match in user_attempts[user_id]
            if not match
        ]

        failed_count = len(failed_attempts)
        total_attempts = len(user_attempts[user_id])

        print(
            f"User {user_id}: {failed_count} failed out of "
            f"{total_attempts} attempts in last {ALERT_TIME_WINDOW}s"
        )

        # Check if we should send an alert email for THIS user
        if failed_count >= MIN_FAILED_ATTEMPTS:
            last_alert = last_alert_sent.get(user_id)
            seconds_since_last = (
                (current_time - last_alert).total_seconds()
                if last_alert is not None
                else None
            )

            if last_alert is None or seconds_since_last > ALERT_EMAIL_COOLDOWN:
                # --- Look up the user's email from userinfo ---
                email = None
                try:
                    cursor = mydb.cursor()
                    cursor.execute(
                        "SELECT email FROM userinfo WHERE userID = %s",
                        (user_id,)
                    )
                    row = cursor.fetchone()
                    cursor.close()

                    if row and row[0]:
                        email = row[0]
                except Exception as e:
                    print("[ALERT] DB error while looking up user email:", e)
                    email = None

                if email:
                    # --- Build and send the security email via SendEmail ---
                    try:
                        timeS = current_time.strftime('%Y-%m-%d %H:%M:%S')

                        body = (
                            "Hello,\n\n"
                            "We detected multiple failed biometric swipes "
                            "associated with your TouchAlytics account.\n\n"
                            f"Failed attempts (last {ALERT_TIME_WINDOW} seconds): {failed_count}\n"
                            f"Time of last attempt: {timeS}\n\n"
                            "If this wasn't you, we recommend changing your password."
                            "— TouchAlytics Security"
                        )
                        SendEmail(
                            email,
                            body,
                            f"TouchAlytics Security Alert - Failed Biometric Swipes - {timeS}",
                        )
                        print(
                            f"[ALERT] Sent failed-attempt security email to {email} "
                            f"for userID {user_id}"
                        )
                    except Exception as e:
                        print("[ALERT] Error sending failed-attempt email:", e)
                else:
                    print(
                        f"[ALERT] No email found for userID {user_id}; "
                        "skipping alert email."
                    )

                # Update cooldown and clear attempt history for THIS user
                last_alert_sent[user_id] = current_time
                user_attempts[user_id] = []
                return True

        return False



@app.route('/authenticate/<user_id>', methods=['POST'])
def authenticate(user_id):
    # Parse JSON
    req = request.get_json()

    if not req:
        print("Invalid JSON")

        return jsonify({"message": "Invalid or missing JSON body"}), 500

    # Validate and collect features in REQUIRED_FEATURES order
    features = []
    for key in REQUIRED_FEATURES:
        if key not in req:
            return jsonify(
                {"message": f"Invalid features provided: missing '{key}'"},
            ), 500
        features.append(req[key])

    # Extract current user ID from the correct position
    user_index = REQUIRED_FEATURES.index("userID")
    currUser = features[user_index]

    # Cast to int so it matches how we trained the model
    try:
        currUser_int = int(currUser)
    except Exception:
        print(f"Warning: could not cast currUser={currUser} to int; using raw value")
        currUser_int = currUser

    # Remove userID from the feature vector
    del features[user_index]

    # --------- NEW: neutral behavior when only one eligible user ----------
    eligible_users, _ = get_eligible_users_and_strokes()
    if len(eligible_users) <= 1:
        # "Neutral" -> no biometric decision; client should fall back to password
        return jsonify({
            "match": "unknown",
            "message": (
                "Biometric model not available: need at least two users with "
                f">= {MIN_STROKES} strokes each."
            ),
        }), 500
    # ----------------------------------------------------------------------

    # We NO LONGER rebuild the model here.
    # We just require that a trained MODEL_FILE already exists.

    if not os.path.exists(MODEL_FILE) or os.path.getsize(MODEL_FILE) == 0:
        return jsonify({
            "match": "unknown",
            "message": "Model file missing or empty. Please train the model."
        }), 500

    # Load the model safely
    try:
        with open(MODEL_FILE, "rb") as f:
            h1 = pickle.load(f)
    except EOFError:
        return jsonify({
            "match": "unknown",
            "message": "Model file is corrupted (EOF)"
        }), 500
    except Exception as e:
        return jsonify({
            "match": "unknown",
            "message": f"Error loading model: {str(e)}"
        }), 500

    # Reshape the features list into a numpy array for prediction
    x_input = np.array(features, dtype=float).reshape(1, -1)

    # Predict with the loaded model
    y_pred = h1.predict(x_input)[0]  # scalar label

    matched_bool = (y_pred == currUser_int)
    matched_str = "true" if matched_bool else "false"
    message = "Matched" if matched_bool else "Not Matched"

    # Track failed attempts using a boolean
    check_failed_attempts(currUser_int, matched_bool)

    if matched_bool:
        # Identity verified
        return jsonify({"match": matched_str, "message": message}), 200
    else:
        # Authentication failed
        return jsonify({"match": matched_str, "message": message}), 400


if __name__ == "__main__":
    # Optional: still try to train on startup.
    # Later, if you want *all* training external, you can delete this block
    # and run create_model() from a separate script/cron instead.
    try:
        create_model()
    except NeedMultipleUsers:
        print("Not enough users with sufficient strokes to train model yet.")
    except ValueError as e:
        print(f"Could not train model on startup: {e}")
    except Exception as e:
        print(f"Unexpected error training model on startup: {e}")

    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
