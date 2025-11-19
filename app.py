# Imports
from datetime import datetime, timedelta
from collections import defaultdict
from threading import Lock

import numpy as np
import pandas as pd
from sklearn import model_selection, svm
from sklearn.metrics import accuracy_score, classification_report
import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, request, jsonify
import pickle
import os

from auth import auth

app = Flask(__name__)

app.register_blueprint(auth)

class NeedMultipleUsers(Exception):
    """Raised when only one user is found"""
    pass



# ------------------------------ CONSTANTS --------------------------

# Constants
MIN_STROKES = 50    # minimum number of touch strokes
TOUCH_ELEMENTS = 31 # Number of elements per touch
USER_ID_FILE = "users.csv"  # File that contains the user ids
MODEL_FILE = "touch_model.pkl"  # File that contains the knn model
# Features required in the swipe authentication request
REQUIRED_FEATURES = [
    "angleChangeRate",
    "averageAcceleration",
    "averageDeceleration",
    "averageDirection",
    "averageTouchArea",
    "averageVelocity",
    "curvature",
    "directionEndToEnd",
    "initPressure",
    "maxIdleTime",
    "maxPressure",
    "maxVelocity",
    "midStrokeArea",
    "midStrokePressure",
    "minPressure",
    "minVelocity",
    "pairwiseVelocityPercentile",
    "pressureChangeRate",
    "pressureVariance",
    "startX",
    "startY",
    "stopX",
    "stopY",
    "straightnessRatio",
    "strokeDuration",
    "touchArea",
    "trajectoryLength",
    "userID",
    "velocityVariance",
    "xdis",
    "ydis"
]

# Alert thresholds
ALERT_TIME_WINDOW = 10 #seconds
MIN_FAILED_ATTEMPTS = 3

# Track failed authentication attempts per user
# Format: {user_id: [(timestamp1, matched), (timestamp2, matched), ...]}
user_attempts = defaultdict(list)
attempts_lock = Lock()

# Track if alert has been sent recently to avoid spam
# Format: {user_id: last_alert_timestamp}
last_alert_sent = {}

# -------------------------- FUNCTIONS ------------------------------

# Retrieves the list of previous users from filename
def get_prev_users(filename):
    try:
        # Read the previous users from the users.csv file
        with open(filename, "r") as ifile:
            prevUsers = ifile.read()

            # Split the csv contents
            prevUsers = prevUsers.split(",")

            # Delete the last blank user
            del prevUsers[-1]
    # Exception for if the file is not found
    except FileNotFoundError:
        prevUsers = []

    return prevUsers

# Gets the current users from the data
# Raises an exception if one user is detected
def get_curr_users(appdata):

    useridlist = []

    appdata
    # Get each userID
    for ID in appdata.keys():
        # Filter users with enough touch strokes
        if len(appdata[ID]) >= MIN_STROKES:
            useridlist.append(ID)

    # Check if only one valid user is detected
    if len(useridlist) <= 1:
        raise NeedMultipleUsers

    return useridlist


def measure_svm_accuracy(X,y):
    # Train and Test sets
    X_train, X_test, y_train, y_test = model_selection.train_test_split(X, y, test_size=0.2, random_state=2)

    # Create the measured model
    h1 = svm.LinearSVC(C=1)  # SVM model

    # h1 = KNeighborsClassifier(n_neighbors = 3) # KNN model
    h1.fit(X_train, y_train)

    # Make predictions
    y_pred = h1.predict(X_test)

    # Print the accuracy of the model
    print(f"Prediction Accuracy: {accuracy_score(y_test, y_pred) * 100:.2f}%")

    # Additional Accuracy Data
    print(f"\nCrosstab Table:\n{pd.crosstab(y_test, y_pred)}")
    print(f"\nClassification Report:\n{classification_report(y_test, y_pred)}")


def create_model():
    # Read data from the database
    ref = db.reference('/')
    data = ref.get()

    print(data)

    # Check if any data exists in the database
    if not data:
        print("No data")
        raise ValueError("No data available in Firebase to train model.")

    # Get the list of previous users
    prevUsers = get_prev_users(USER_ID_FILE)

    # Get the list of current users (may raise NeedMultipleUsers)
    userIDlist = get_curr_users(data)

    # If no change in users AND a valid model file already exists, nothing to do
    if (
        set(userIDlist) == set(prevUsers)
        and os.path.exists(MODEL_FILE)
        and os.path.getsize(MODEL_FILE) > 0
    ):
        return None  # model is already up to date

    # Remake the model
    X = np.array([])
    y = np.array([])

    # Feature order for X: all REQUIRED_FEATURES except "userID"
    feature_keys = [k for k in REQUIRED_FEATURES if k != "userID"]

    # Open a csv file to keep track of users
    with open(USER_ID_FILE, "w") as file:
        touchIDCount = 0

        # Cycle through each user id
        for userID in userIDlist:
            file.write(f"{userID},")
            # Break down touchID dictionary
            for touchID in data[userID].keys():
                touchIDCount += 1
                stroke = data[userID][touchID]

                # Label: userID for this stroke
                y = np.append(y, stroke["userID"])

                # Features: all keys except "userID", in REQUIRED_FEATURES order
                for key in feature_keys:
                    X = np.append(X, stroke[key])

    if touchIDCount == 0:
        print("No valid data")
        raise ValueError("No valid touch data to train model.")

    # Reshape X: one row per touch, one column per feature (excluding userID)
    X = X.reshape(touchIDCount, len(feature_keys))

    # Evaluate SVM accuracy
    measure_svm_accuracy(X, y)

    # Create the full SVM model
    h1 = svm.LinearSVC(C=1)
    h1.fit(X, y)

    # Save the model
    with open(MODEL_FILE, 'wb') as f:
        pickle.dump(h1, f)

    return h1




# -------------------------------------------------------------------

def check_failed_attempts(user_id, matched):
    """
    Track authentication attempts and trigger email alert if threshold is exceeded
    :param user_id: The user ID being authenticated
    :param matched: Boolean indicating if authentication succeeded
    :return: Boolean indicating if an alert was triggered
    """
    current_time = datetime.now()

    with attempts_lock:
        # Add current attempt
        user_attempts[user_id].append((current_time, matched))

        # Remove attempts older than the time window
        cutoff_time = current_time - timedelta(seconds = ALERT_TIME_WINDOW)
        user_attempts[user_id] = [
            (timestamp, match) for timestamp, match in user_attempts[user_id]
            if timestamp > cutoff_time
        ]

        # Count failed attempts in the time window
        failed_attempts = [
            match for timestamp, match in user_attempts[user_id]
            if not match
        ]

        failed_count = len(failed_attempts)
        total_attempts = len(user_attempts[user_id])

        print(f"User {user_id}: {failed_count} failed out of {total_attempts} attempts in last {ALERT_TIME_WINDOW}s")

        # Check if we should send an alert
        if failed_count >= MIN_FAILED_ATTEMPTS:
            # Check if we haven't sent an alert recently (within last 60 seconds)
            last_alert = last_alert_sent.get(user_id)
            if last_alert is None or (current_time - last_alert).total_seconds() > 60:
                # Print alert message instead of sending email
                print("\n" + "=" * 60)
                print("⚠️  SECURITY ALERT - EMAIL WOULD BE SENT")
                print("=" * 60)
                print(f"User ID: {user_id}")
                print(f"Failed Attempts: {failed_count}")
                print(f"Time Window: {ALERT_TIME_WINDOW} seconds")
                print(f"Timestamp: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print("=" * 60 + "\n")

                last_alert_sent[user_id] = current_time
                # Clear attempts after triggering alert
                user_attempts[user_id] = []
                return True

        return False

@app.route('/authenticate/<user_id>', methods=['POST'])
def authenticate(user_id):
    # Parse JSON
    req = request.get_json()

    # print(req)

    if not req:
        print("Invalid JSON")
        return jsonify({"message": "Invalid or missing JSON body"}), 400

    # Validate and collect features in order
    features = []
    for key in REQUIRED_FEATURES:
        if key not in req:
            return jsonify(
                {"message": f"Invalid features provided: missing '{key}'"},
            ), 400
        features.append(req[key])

    # Extract current user ID from the correct position
    user_index = REQUIRED_FEATURES.index("userID")
    currUser = features[user_index]
    del features[user_index]  # remove userID from the feature vector

    # Get the list of previous users
    prevUsers = get_prev_users(USER_ID_FILE)

    # Decide if we need to rebuild the model
    need_rebuild = False

    # No previous users -> definitely need to build model
    if len(prevUsers) == 0:
        need_rebuild = True
    # New user not seen in prevUsers -> rebuild model
    elif currUser not in prevUsers:
        need_rebuild = True
    # Model file missing or empty -> rebuild model
    elif (not os.path.exists(MODEL_FILE)) or (os.path.getsize(MODEL_FILE) == 0):
        need_rebuild = True

    if need_rebuild:
        try:
            create_model()
        except NeedMultipleUsers as e:
            return jsonify({
                "match": "false",
                "message": f"Need more users: {str(e)}"
            }), 400
        except ValueError as e:
            # e.g. no data / no valid touches
            return jsonify({
                "match": "false",
                "message": f"Could not train model: {str(e)}"
            }), 503
        except Exception as e:
            return jsonify({
                "match": "false",
                "message": f"Error while training model: {str(e)}"
            }), 503

    # At this point we expect a valid, non-empty MODEL_FILE
    if not os.path.exists(MODEL_FILE) or os.path.getsize(MODEL_FILE) == 0:
        return jsonify({
            "match": "false",
            "message": "Model file missing or empty"
        }), 503

    # Load the model safely
    try:
        with open(MODEL_FILE, "rb") as f:
            h1 = pickle.load(f)
    except EOFError:
        return jsonify({
            "match": "false",
            "message": "Model file is corrupted (EOF)"
        }), 503
    except Exception as e:
        return jsonify({
            "match": "false",
            "message": f"Error loading model: {str(e)}"
        }), 503

    # Reshape the features list into a numpy array for prediction
    x_input = np.array(features).reshape(1, -1)

    # Predict with the loaded model
    y_pred = h1.predict(x_input)

    if y_pred == currUser:
        matched = "true"
        message = "Matched"
    else:
        matched = "false"
        message = "Not Matched"

    check_failed_attempts(currUser, matched)
    
    return jsonify({"match": matched, "message": message}), 200





if __name__ == "__main__":
    # Path to the service account key file
    cred = credentials.Certificate("firebase_service_key.json")
    firebase_admin.initialize_app(cred, {'databaseURL': 'https://touchalytics-fedb3-default-rtdb.firebaseio.com/'})

    try:
        create_model()

    except ImportError:
        # Do nothing
        print()
    except ValueError:
        # Do nothing
        print()

    app.run(host='0.0.0.0', port=5000, debug=True)





