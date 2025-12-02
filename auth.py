import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import mysql.connector
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Blueprint
import random
import os
from authlib.integrations.flask_client import OAuth  # pip install flask requests authlib
from dotenv import load_dotenv
from datetime import datetime, timedelta

import socket
import json
from threading import Thread

auth = Blueprint("auth", __name__)

# ------------------- MySQL setup -------------------
mydb = mysql.connector.connect(
    host="localhost",
    user="TouchAlytics",
    password="Touchgroup1!",
    database="touchalytics"
)
mycursor = mydb.cursor()

# ------------------- Globals -----------------------
received_email = False
token = str(random.randint(100000, 999999))

HOST = "0.0.0.0"
PORT = 7000  # must match Android SERVER_PORT

# Max number of swipe feature rows to store per user
MAX_SWIPEFEATURE_ROWS_PER_USER = 90  # e.g., 30 + 40 + 20


# ------------------- Helper functions --------------

def SendEmail(email, body, sub):
    """Send a plain-text email via Gmail SMTP."""
    s = smtplib.SMTP('smtp.gmail.com', 587)
    s.starttls()
    s.login("ee368project@gmail.com", "agbk izdf kpfe ssby")

    msg = MIMEMultipart()
    msg['Subject'] = sub
    msg.attach(MIMEText(body, 'plain'))

    s.sendmail("ee368project@gmail.com", email, msg.as_string())
    s.quit()


def is_valid_email(email):
    return "@" in email and "." in email


# ------------------- Socket server -----------------

def socket_server():
    """
    Background TCP server that:
      - handles email-only messages (registration),
      - handles credentials messages "email|hashedPassword|deviceId",
      - handles JSON swipe feature payloads.
    """
    global received_email, token

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen(5)
    print(f"[SOCKET] Listening on {HOST}:{PORT}")

    while True:
        conn, addr = server.accept()
        print(f"[SOCKET] Connected by: {addr}")

        with conn:
            try:
                # Allow multiple messages per connection
                while True:
                    data = conn.recv(4096)
                    if not data:
                        # client closed connection
                        break

                    text = data.decode('utf-8').strip()
                    if not text:
                        continue

                    print("[SOCKET] Raw received:", repr(text))

                    # ───────── CASE 1: "email|flag" (registration / forgot password) ─────────
                    # Android now sends: "<email>|<flag>" where flag is "exists" or "dne"
                    if "|" in text and not text.startswith(("STORE|", "CHECK|", "UPDATE|", "FCOUNT|", "FSTORE|")):

                        try:
                            email_part, mode = text.split("|", 1)
                        except ValueError:
                            print("[SOCKET] Malformed email/flag payload:", text)
                            resp = json.dumps({"status": "error", "message": "bad_payload"})
                            conn.sendall(resp.encode("utf-8"))
                            continue

                        email = email_part.strip()
                        mode = mode.strip().lower()

                        if not is_valid_email(email):
                            print("[SOCKET] Invalid email format in email/flag payload:", email)
                            resp = json.dumps({"status": "error", "message": "bad_email"})
                            conn.sendall(resp.encode("utf-8"))
                            continue

                        print(f"[SOCKET] Email/flag payload received: email={email!r}, mode={mode!r}")

                        try:
                            # Check if email already exists in userinfo
                            check_sql = "SELECT 1 FROM userinfo WHERE email = %s LIMIT 1"
                            mycursor.execute(check_sql, (email,))
                            exists = mycursor.fetchone() is not None

                            if exists:
                                print("[SOCKET] Email already exists in userinfo:", email)

                                # Forgot-password flow: only send email when client asked for "exists"
                                if mode == "exists":
                                    # Make a NEW token every time we send an email
                                    token = str(random.randint(100000, 999999))
                                    emailMess = (
                                        "You requested to reset your TouchAlytics password.\n\n"
                                        f"Verification Code: {token}\n\n\n"
                                        "If you did not request this, you can ignore this email."
                                    )
                                    SendEmail(email, emailMess, f"Password Reset Verification - [{token}]")
                                    print("[SOCKET] Sent password reset token:", token)

                                # Android forgot-password treats "exists" as the good path
                                resp = json.dumps({"status": "exists", "token": int(token)})
                                conn.sendall(resp.encode("utf-8"))

                            else:
                                print("[SOCKET] New email, candidate for registration:", email)

                                # Registration flow: only send email when client asked for "dne"
                                if mode == "dne":
                                    token = str(random.randint(100000, 999999))
                                    emailMess = (
                                        "To complete your registration for your EE368Project account "
                                        "please use the following verification code.\n\n"
                                        f"Verification Code: {token}\n\n\n"
                                        "If you are not trying to register this email address, "
                                        "please ignore this."
                                    )
                                    SendEmail(email, emailMess, f"Email Verification - [{token}]")
                                    print("[SOCKET] Sent registration email token:", token)

                                # Android registration treats "ok" as the good path
                                resp = json.dumps({"status": "ok", "token": int(token)})
                                conn.sendall(resp.encode("utf-8"))

                        except mysql.connector.Error as e:
                            print("[SOCKET] MySQL error during email existence check:", e)
                            resp = json.dumps({"status": "error", "message": "db_error"})
                            conn.sendall(resp.encode("utf-8"))

                        continue

                    # ───────── CASE 2: STORE credentials "STORE|email|passwordHash|deviceId" ─────────
                    if text.startswith("STORE|"):
                        parts = text.split("|", 4)
                        if len(parts) == 4:
                            _, email, hashed_password, device_id = parts

                            print("[SOCKET] STORE request received:")
                            print("   email:          ", email)
                            print("   hashed_password:", hashed_password)
                            print("   device_id:      ", device_id)

                            try:
                                sql = """
                                    INSERT INTO userinfo (email, password, deviceID)
                                    VALUES (%s, %s, %s)
                                """
                                mycursor.execute(sql, (email, hashed_password, device_id))
                                mydb.commit()

                                # Get the auto-incremented userID from this insert
                                user_id = mycursor.lastrowid
                                print("[SOCKET] Credentials inserted into userinfo table with userID:", user_id)

                                resp = json.dumps({"status": "stored", "userID": int(user_id)})
                                conn.sendall(resp.encode('utf-8'))

                            except mysql.connector.Error as e:
                                print("[SOCKET] MySQL error while storing credentials:", e)
                                resp = json.dumps({"status": "error", "message": "db_error"})
                                conn.sendall(resp.encode('utf-8'))

                        else:
                            print("[SOCKET] Malformed STORE payload:", text)
                            resp = json.dumps({"status": "error", "message": "bad_payload"})
                            conn.sendall(resp.encode('utf-8'))

                        continue
                    # ───────── CASE 3: CHECK credentials "CHECK|email|passwordHash|deviceId" ─────────
                    if text.startswith("CHECK|"):
                        parts = text.split("|", 4)
                        if len(parts) == 4:
                            _, email, hashed_password, device_id = parts

                            print("[SOCKET] CHECK request received:")
                            print("   email:          ", email)
                            print("   hashed_password:", hashed_password)
                            print("   device_id:      ", device_id)

                            try:
                                # 1) Check if email + password match exactly
                                sql = "SELECT userID, deviceID FROM userinfo WHERE email = %s AND password = %s LIMIT 1"
                                mycursor.execute(sql, (email, hashed_password))
                                row = mycursor.fetchone()

                                if row is None:
                                    print("[SOCKET] Invalid login (email/password mismatch) for:", email)
                                    resp = json.dumps({
                                        "status": "error",
                                        "message": "Invalid email or password"
                                    })
                                    conn.sendall(resp.encode('utf-8'))
                                    continue

                                user_id, stored_device_id = row
                                print("[SOCKET] Login matched userID:", user_id)

                                # 2) If device ID does not match, send alert email
                                if stored_device_id != device_id:
                                    print("[SOCKET] Device mismatch for userID:", user_id)
                                    try:
                                        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                        body = (
                                            f"Hello,\n\n"
                                            f"Your TouchAlytics credentials were used to log in from a different device.\n\n"
                                            f"Time: {now}\n"
                                            f"Stored device ID: {stored_device_id}\n"
                                            f"New device ID:    {device_id}\n\n"
                                            f"If this wasn't you, please change your password."
                                        )
                                        SendEmail(email, body, "TouchAlytics New Device Login Alert")
                                        print("[SOCKET] New device login email sent to:", email)
                                    except Exception as e:
                                        print("[SOCKET] Failed to send new device alert email:", e)

                                # 3) Count number of features for this user (placeholder for now)
                                try:
                                    count_sql = "SELECT COUNT(*) FROM swipefeatures WHERE userID = %s"
                                    mycursor.execute(count_sql, (user_id,))
                                    row_cnt = mycursor.fetchone()
                                    features_count = int(row_cnt[0]) if row_cnt and row_cnt[0] is not None else 0
                                    print(f"[SOCKET] Found {features_count} swipefeature rows for userID {user_id}")
                                except mysql.connector.Error as e:
                                    print("[SOCKET] MySQL error when counting features:", e)
                                    features_count = 0

                                resp = json.dumps({
                                    "status": "good",
                                    "userID": int(user_id),
                                    "features": int(features_count)
                                })
                                conn.sendall(resp.encode('utf-8'))

                            except mysql.connector.Error as e:
                                print("[SOCKET] MySQL error during CHECK:", e)
                                resp = json.dumps({
                                    "status": "error",
                                    "message": "db_error"
                                })
                                conn.sendall(resp.encode('utf-8'))

                        else:
                            print("[SOCKET] Malformed CHECK payload:", text)
                            resp = json.dumps({"status": "error", "message": "bad_payload"})
                            conn.sendall(resp.encode('utf-8'))

                        continue

                    # ───────── CASE 4: UPDATE password "UPDATE|email|passwordHash|deviceId" ─────────
                    if text.startswith("UPDATE|"):
                        parts = text.split("|", 4)
                        if len(parts) == 4:
                            _, email, hashed_password, device_id = parts

                            print("[SOCKET] UPDATE request received:")
                            print("   email:          ", email)
                            print("   hashed_password:", hashed_password)
                            print("   device_id:      ", device_id)

                            try:
                                # 1) Fetch current password hash for this email
                                sql = "SELECT password FROM userinfo WHERE email = %s LIMIT 1"
                                mycursor.execute(sql, (email,))
                                row = mycursor.fetchone()

                                if row is None:
                                    print("[SOCKET] UPDATE requested for non-existent email:", email)
                                    resp = json.dumps({
                                        "status": "error",
                                        "message": "no_such_email"
                                    })
                                    conn.sendall(resp.encode("utf-8"))
                                    continue

                                current_hash = row[0]

                                # 2) Compare hashes
                                if current_hash == hashed_password:
                                    print("[SOCKET] UPDATE password matches existing password for:", email)
                                    resp = json.dumps({
                                        "status": "error",
                                        "message": "Cannot use most current password!"
                                    })
                                    conn.sendall(resp.encode("utf-8"))
                                    continue

                                # 3) Update the stored password
                                update_sql = "UPDATE userinfo SET password = %s WHERE email = %s"
                                mycursor.execute(update_sql, (hashed_password, email))
                                mydb.commit()

                                print("[SOCKET] Password updated for:", email)
                                resp = json.dumps({"status": "ok"})
                                conn.sendall(resp.encode("utf-8"))

                            except mysql.connector.Error as e:
                                print("[SOCKET] MySQL error during UPDATE:", e)
                                resp = json.dumps({
                                    "status": "error",
                                    "message": "db_error"
                                })
                                conn.sendall(resp.encode("utf-8"))

                        else:
                            print("[SOCKET] Malformed UPDATE payload:", text)
                            resp = json.dumps({"status": "error", "message": "bad_payload"})
                            conn.sendall(resp.encode("utf-8"))

                        continue

                    # ───────── CASE 5: FCOUNT|<userID>  (return total stored swipes) ─────────
                    if text.startswith("FCOUNT|"):
                        parts = text.split("|", 1)
                        if len(parts) != 2:
                            print("[SOCKET] Malformed FCOUNT payload:", text)
                            # Send "0" so Android treats it as incomplete training
                            conn.sendall(b"0")
                            continue

                        raw_user_id = parts[1].strip()
                        try:
                            user_id = int(raw_user_id)
                        except ValueError:
                            print("[SOCKET] Invalid userID in FCOUNT payload:", raw_user_id)
                            conn.sendall(b"0")
                            continue

                        try:
                            count_sql = "SELECT COUNT(*) FROM swipefeatures WHERE userID = %s"
                            mycursor.execute(count_sql, (user_id,))
                            row = mycursor.fetchone()
                            total_count = int(row[0]) if row and row[0] is not None else 0
                            print(f"[SOCKET] FCOUNT for userID {user_id}: {total_count}")

                            # ── NEW: if this user has reached the per-user cap, try to (re)build the model ──
                            if total_count == MAX_SWIPEFEATURE_ROWS_PER_USER:
                                print(
                                    f"[SOCKET] User {user_id} reached "
                                    f"{MAX_SWIPEFEATURE_ROWS_PER_USER} strokes; attempting model rebuild..."
                                )
                                try:
                                    # Lazy import to avoid circular import at module load time
                                    from app import create_model, NeedMultipleUsers
                                    try:
                                        create_model()
                                        print("[SOCKET] Model retrained successfully after user reached cap.")
                                    except NeedMultipleUsers:
                                        # Not enough users with ≥ MIN_STROKES yet; safe to ignore
                                        print(
                                            "[SOCKET] Not enough fully-trained users "
                                            "to build model yet (NeedMultipleUsers)."
                                        )
                                    except Exception as e:
                                        print("[SOCKET] Error while retraining model from FCOUNT:", e)
                                except ImportError as e:
                                    print("[SOCKET] Could not import create_model from app:", e)

                            # Send the count back to Android
                            conn.sendall(str(total_count).encode("utf-8"))

                        except mysql.connector.Error as e:
                            print("[SOCKET] MySQL error during FCOUNT:", e)
                            # On error, send "0" so Android sees it as not enough training
                            conn.sendall(b"0")

                        continue

                    # ───────── CASE 6: FSTORE|<json swipe features> ─────────
                    if text.startswith("FSTORE|"):
                        _, json_str = text.split("|", 1)
                        try:
                            features = json.loads(json_str)
                            print("[SOCKET] Received Features JSON via FSTORE:", features)

                            # Expect JSON keys that MATCH DB column names:
                            # userID, strokeDuration, midStrokeArea, midStrokePress,
                            # dirEndToEnd, aveDir, aveVelo, pairwiseVeloPercent,
                            # startX, startY, stopX, stopY, touchArea,
                            # maxVelo, minVelo, accel, decel, trajLength,
                            # curvature, veloVariance, angleChangeRate,
                            # maxPress, minPress, initPress, pressChangeRate,
                            # pressVariance, maxIdleTime, straightnessRatio,
                            # xDisplacement, yDisplacement, aveTouchArea

                            # ---- Extract userID and enforce per-user cap ----
                            try:
                                user_id = int(features.get("userID", -1))
                            except (TypeError, ValueError):
                                user_id = -1

                            if user_id <= 0:
                                print("[SOCKET] FSTORE payload missing/invalid userID:", features.get("userID"))
                                resp = json.dumps({"status": "error", "message": "bad_user_id"})
                                conn.sendall(resp.encode("utf-8"))
                                continue

                            try:
                                mycursor.execute(
                                    "SELECT COUNT(*) FROM swipefeatures WHERE userID = %s",
                                    (user_id,)
                                )
                                row_cnt = mycursor.fetchone()
                                current_count = int(row_cnt[0]) if row_cnt and row_cnt[0] is not None else 0
                                print(
                                    f"[SOCKET] FSTORE current swipefeatures count for user {user_id}: {current_count}")

                                if current_count >= MAX_SWIPEFEATURE_ROWS_PER_USER:
                                    print(
                                        f"[SOCKET] Cap reached for user {user_id}: "
                                        f"{current_count} >= {MAX_SWIPEFEATURE_ROWS_PER_USER}, skipping insert."
                                    )
                                    resp = json.dumps({
                                        "status": "features_ignored_cap_reached",
                                        "userID": user_id,
                                        "count": current_count,
                                        "cap": MAX_SWIPEFEATURE_ROWS_PER_USER,
                                    })
                                    conn.sendall(resp.encode("utf-8"))
                                    continue

                            except mysql.connector.Error as e:
                                print("[SOCKET] MySQL error while checking swipefeatures cap:", e)
                                resp = json.dumps({"status": "error", "message": "db_error"})
                                conn.sendall(resp.encode("utf-8"))
                                continue

                            # ---- Under cap: proceed to insert this stroke ----
                            sql = """
                            INSERT INTO swipefeatures (
                                userID,
                                strokeDuration,
                                midStrokeArea,
                                midStrokePress,
                                dirEndToEnd,
                                aveDir,
                                aveVelo,
                                pairwiseVeloPercent,
                                startX,
                                startY,
                                stopX,
                                stopY,
                                touchArea,
                                maxVelo,
                                minVelo,
                                accel,
                                decel,
                                trajLength,
                                curvature,
                                veloVariance,
                                angleChangeRate,
                                maxPress,
                                minPress,
                                initPress,
                                pressChangeRate,
                                pressVariance,
                                maxIdleTime,
                                straightnessRatio,
                                xDisplacement,
                                yDisplacement,
                                aveTouchArea
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s
                            )
                            """

                            vals = (
                                features.get("userID"),
                                features.get("strokeDuration"),
                                features.get("midStrokeArea"),
                                features.get("midStrokePress"),
                                features.get("dirEndToEnd"),
                                features.get("aveDir"),
                                features.get("aveVelo"),
                                features.get("pairwiseVeloPercent"),
                                features.get("startX"),
                                features.get("startY"),
                                features.get("stopX"),
                                features.get("stopY"),
                                features.get("touchArea"),
                                features.get("maxVelo"),
                                features.get("minVelo"),
                                features.get("accel"),
                                features.get("decel"),
                                features.get("trajLength"),
                                features.get("curvature"),
                                features.get("veloVariance"),
                                features.get("angleChangeRate"),
                                features.get("maxPress"),
                                features.get("minPress"),
                                features.get("initPress"),
                                features.get("pressChangeRate"),
                                features.get("pressVariance"),
                                features.get("maxIdleTime"),
                                features.get("straightnessRatio"),
                                features.get("xDisplacement"),
                                features.get("yDisplacement"),
                                features.get("aveTouchArea"),
                            )

                            mycursor.execute(sql, vals)
                            mydb.commit()
                            print("[SOCKET] Inserted features into swipefeatures successfully.")

                            resp = json.dumps({"status": "features_received"})
                            conn.sendall(resp.encode("utf-8"))

                        except json.JSONDecodeError as e:
                            print("[SOCKET] JSON decode error in FSTORE:", e)
                            print("[SOCKET] Raw data:", json_str)
                            resp = json.dumps({"status": "error", "message": "bad_json"})
                            conn.sendall(resp.encode("utf-8"))

                        except mysql.connector.Error as e:
                            print("[SOCKET] MySQL error inserting features:", e)
                            resp = json.dumps({"status": "error", "message": "db_error"})
                            conn.sendall(resp.encode("utf-8"))

                        continue

            except Exception as e:
                print("[SOCKET] Handler error:", e)


# ------------------- Blueprint hooks & routes ----------------------

@auth.record
def start_socket_server(setup_state):
    """
    This runs once when the blueprint is registered on the Flask app.
    It starts the background socket server thread.
    """
    print("[SOCKET] Starting background socket server thread...")
    t = Thread(target=socket_server, daemon=True)
    t.start()


@auth.route("/listen")
def listen():
    """
    HTTP endpoint used by the Android app to fetch the current token.
    """
    # token is a global string like "123456"
    return jsonify({"token": int(token)}), 200


