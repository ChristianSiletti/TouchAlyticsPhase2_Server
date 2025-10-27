import mysql.connector
from flask import Flask, render_template, request, redirect, url_for, session, flash
import random
import os
from authlib.integrations.flask_client import OAuth  # pip install flask requests authlib
from dotenv import load_dotenv
from datetime import datetime, timedelta
import socket
import json


mydb = mysql.connector.connect(
    host="localhost",
    user="TouchAlytics",
    password="Touchgroup1!",
    database="touchalytics"
)

mycursor = mydb.cursor()



HOST = "0.0.0.0"
PORT = 7000

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind((HOST, PORT))
server.listen(1)
print("Server listening...")

conn, addr = server.accept()
print(f"Connected by: {addr}")

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

conn.close()
server.close()
mydb.close()
