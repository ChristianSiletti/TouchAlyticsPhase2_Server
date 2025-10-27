# Imports
import numpy as np
import pandas as pd
from sklearn import model_selection, svm
from sklearn.metrics import accuracy_score, classification_report
import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, request, jsonify
import pickle
import os

app = Flask(__name__)

# ------------------------------ CONSTANTS --------------------------

# Constants
MIN_STROKES = 50    # minimum number of touch strokes
TOUCH_ELEMENTS = 19 # Number of elements per touch
USER_ID_FILE = "users.csv"  # File that contains the user ids
MODEL_FILE = "touch_model.pkl"  # File that contains the knn model
# Features required in the swipe authentication request
REQUIRED_FEATURES = [
    "averageAcceleration",
    "averageDeceleration",
    "averageDirection",
    "averageVelocity",
    "curvature",
    "directionEndToEnd",
    "maxVelocity",
    "midStrokeArea",
    "midStrokePressure",
    "minVelocity",
    "pairwiseVelocityPercentile",
    "startX",
    "startY",
    "stopX",
    "stopY",
    "strokeDuration",
    "touchArea",
    "trajectoryLength",
    "userID"
]

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

    # Get each userID
    for ID in appdata.keys():
        # Filter users with enough touch strokes
        if len(appdata[ID]) >= MIN_STROKES:
            useridlist.append(ID)

    # Check if only one valid user is detected
    if len(useridlist) <= 1:
        raise ValueError

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

    # Data is a dictionary of user id keys, with values of a dictionaries with keys of
    # touch stroke ids, with values of dictionaries of each touch stroke data component
    data = ref.get()

    # print(len(data))
    # print(data)

    # Check if any data exists in the database
    if data is None:
        try:
            # Delete the previous users just in case
            os.remove(USER_ID_FILE)
        except FileNotFoundError:
            print("File No longer exists")

        raise ImportError

    # Get the list of previous users
    prevUsers = get_prev_users(USER_ID_FILE)

    # Get the list of current users
    userIDlist = get_curr_users(data)

    # Check if any new users were added
    if set(userIDlist) != set(prevUsers):
        # Remake the model if new users exist

        # Initialize valid data into X
        X = np.array([])

        # Initialize valid output data into y
        y = np.array([])

        # Open a csv file to keep track of users
        file = open(USER_ID_FILE, "w")

        # Total touch count across all valid users
        touchIDCount = 0

        # Organize the input and output data into X and y

        # Cycle through each user id
        for userID in userIDlist:
            # Write the user to the csv
            file.write(f"{userID},")
            # Break down touchID dictionary
            for touchID in data[userID].keys():
                touchIDCount = touchIDCount + 1

                # Reset the element counter
                elmCount = 1

                # Break down information on touch dictionary
                for info in data[userID][touchID].keys():
                    if elmCount % TOUCH_ELEMENTS == 0:
                        # Store the info in y
                        y = np.append(y, data[userID][touchID][info])
                    else:
                        # Store the info in X
                        X = np.append(X, data[userID][touchID][info])
                    # Increment the element counter
                    elmCount = elmCount + 1

        # Close the csv file
        file.close()

        # Reshape X
        X = X.reshape(touchIDCount, TOUCH_ELEMENTS - 1)

        # Make a svm model and measure its accuracy
        measure_svm_accuracy(X, y)

        # Create the full SVM model
        h1 = svm.LinearSVC(C=1)
        h1.fit(X, y)

        # Save the model
        pickle.dump(h1, open(MODEL_FILE, 'wb'))



# -------------------------------------------------------------------


@app.route('/authenticate/<user_id>', methods=['POST'])
def authenticate(user_id):
    # Parse JSON
    req = request.get_json()

    if not req:
        return jsonify({"message": "Invalid or missing JSON body"}), 400


    # Validate features
    features = []
    for key in REQUIRED_FEATURES:
        if key not in req:
            return jsonify({"message": f"Invalid features provided: missing '{key}'"}), 400
        # Add feature to list
        features.append(req[key])

    # Get the list of previous users
    prevUsers = get_prev_users(USER_ID_FILE)

    currUser = features[TOUCH_ELEMENTS-1]

    del features[TOUCH_ELEMENTS-1]

    if len(prevUsers) == 0:
        try:
            # Attempt to create model
            create_model()
        except ValueError:
            return jsonify({"match": "true", "message": "Matched"}), 200
    else:
        # Check if its not a new user
        if currUser not in prevUsers:
            # Remake model
            create_model()

    # Load the model
    h1 = pickle.load(open(MODEL_FILE, 'rb'))

    # Reshape the features list into a numpy
    x_input = np.array(features).reshape(1,-1)

    y_pred = h1.predict(x_input)

    # print(y_pred)

    if y_pred == currUser:
        matched = "true"
        message = "Matched"
    else:
        matched = "false"
        message = "Not Matched"

    # print(matched,message)

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



