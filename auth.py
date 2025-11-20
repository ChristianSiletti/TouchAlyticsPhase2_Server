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

                    # ───────── CASE 1: plain email (registration: check existence in userinfo) ─────────
                    if is_valid_email(text) and "|" not in text:
                        email = text
                        print("[SOCKET] Registration email received:", email)

                        try:
                            # Check if email already exists in userinfo
                            check_sql = "SELECT 1 FROM userinfo WHERE email = %s LIMIT 1"
                            mycursor.execute(check_sql, (email,))
                            exists = mycursor.fetchone() is not None

                            if exists:
                                print("[SOCKET] Email already exists in userinfo:", email)
                                resp = json.dumps({"status": "exists"})
                                conn.sendall(resp.encode('utf-8'))
                            else:
                                print("[SOCKET] New email, sending verification token:", email)

                                emailMess = (
                                    "To complete your registration for your EE368Project account please use the following verification code.\n\n"
                                    f"Verification Code: {token}\n\n\n"
                                    "If you are not trying to register this email address, please ignore this."
                                )
                                SendEmail(email, emailMess, f"Email Verification - [{token}]")
                                print("[SOCKET] Sent email token:", token)

                                # Send status + token back to Android
                                resp = json.dumps({"status": "ok", "token": int(token)})
                                conn.sendall(resp.encode('utf-8'))

                        except mysql.connector.Error as e:
                            print("[SOCKET] MySQL error during email existence check:", e)
                            resp = json.dumps({"status": "error", "message": "db_error"})
                            conn.sendall(resp.encode('utf-8'))

                        continue

                    # ───────── CASE 2: STORE credentials "STORE|email|passwordHash|deviceId" ─────────
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

                    # ───────── CASE 4: assume JSON swipe features ─────────
                    try:
                        features = json.loads(text)
                        print("[SOCKET] Received Features JSON:", features)

                        sql = """
                        INSERT INTO swipefeatures
                        (userID, strokeDuration, midStrokeArea, midStrokePress, dirEndToEnd, aveDir,
                         aveVelo, pairwiseVeloPercent, startX, stopX, startY, stopY, touchArea, maxVelo, 
                         minVelo, accel, decel, trajLength, curvature, veloVariance, angleChangeRate, maxPress,
                         minPress, initPress, pressChangeRate, pressVariance) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, 
                                %s, %s)
                        """

                        vals = (
                            features.get("userID"),
                            features.get("strokeDuration"),
                            features.get("midStrokeArea"),
                            features.get("midStrokePressure"),
                            features.get("directionEndToEnd"),
                            features.get("averageDirection"),
                            features.get("averageVelocity"),
                            features.get("pairwiseVelocityPercentile"),
                            features.get("startX"),
                            features.get("stopX"),
                            features.get("startY"),
                            features.get("stopY"),
                            features.get("touchArea"),
                            features.get("maxVelo"),
                            features.get("minVelo"),
                            features.get("aveAccel"),
                            features.get("aveDecel"),
                            features.get("trajLength"),
                            features.get("curvature"),
                            features.get("veloVariance"),
                            features.get("angleChangeRate"),
                            features.get("maxPress"),
                            features.get("minPress"),
                            features.get("initPress"),
                            features.get("pressChangeRate"),
                            features.get("pressVariance"),
                        )

                        mycursor.execute(sql, vals)
                        mydb.commit()
                        print("[SOCKET] Inserted features into MySQL successfully.")

                        resp = json.dumps({"status": "features_received"})
                        conn.sendall(resp.encode('utf-8'))

                    except json.JSONDecodeError as e:
                        print("[SOCKET] JSON decode error:", e)
                        print("[SOCKET] Raw data:", text)
                    except mysql.connector.Error as e:
                        print("[SOCKET] MySQL error:", e)

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


