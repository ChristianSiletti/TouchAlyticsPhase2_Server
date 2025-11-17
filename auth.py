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

auth = Blueprint("auth", __name__)



mydb = mysql.connector.connect(
    host="localhost",
    user="TouchAlytics",
    password="Touchgroup1!",
    database="touchalytics"
)

mycursor = mydb.cursor()

token = str(random.randint(100000, 999999))

def SendEmail(email,body,sub):
    # creates SMTP session
    s = smtplib.SMTP('smtp.gmail.com', 587)
    # start TLS for security
    s.starttls()
    # Authentication
    s.login("ee368project@gmail.com", "agbk izdf kpfe ssby")
    # Create a MIMEText object to represent the email
    msg = MIMEMultipart()
    msg['Subject'] = sub

    # Attach the body of the email to the message
    msg.attach(MIMEText(body, 'plain'))
    s.sendmail("ee368project@gmail.com", email, msg.as_string())
    # terminating the session
    s.quit()

def is_valid_email(email):
    return "@" in email and "." in email

@auth.route("/get_token")
def sendToAndroid():
    listen()
    return jsonify({"token": token})


def listen():
    HOST = "0.0.0.0"
    PORT = 7000
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen(1)
    print("Server listening...")
    conn, addr = server.accept()
    print(f"Connected by: {addr}")

    data = conn.recv(4096)
    text = data.decode('utf-8').strip()

    if (is_valid_email(text)):
        emailMess = (
                        "To complete your registration for your EE368Project account please use the following verification code.\n\n"
                        "Verification Code: ") + token + (
                        "\n\n\nIf you are not trying to register this email address, please ignore this.")
        SendEmail(text, emailMess, "Email Verification - [" + token + "]")


    else:

        while True:
            data = conn.recv(4096)
            if not data:
                break

            text = data.decode('utf-8').strip()
            if not text:
                continue

            try:
                features = json.loads(text)
                print("Received Features:", features)

                # ---------- Insert into MySQL ----------
                sql = """
                INSERT INTO swipefeatures
                (userID, strokeDuration, midStrokeArea, midStrokePress, dirEndToEnd, aveDir,
                aveVelo, pairwiseVeloPercent, startX, stopX, startY, stopY)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    features.get("stopY")
                )

                mycursor.execute(sql, vals)
                mydb.commit()
                print("Inserted into MySQL successfully.")

                # Send acknowledgment to client
                response = json.dumps({"status": "received"})
                conn.sendall(response.encode('utf-8'))

            except json.JSONDecodeError as e:
                print("JSON decode error:", e)
                print("Raw data:", text)
            except mysql.connector.Error as e:
                print("MySQL error:", e)

        mydb.close()
    conn.close()
    server.close()
    # sendToAndroid(token)
    print(token)
