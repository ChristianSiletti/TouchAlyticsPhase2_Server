import mysql.connector
from flask import Flask, render_template, request, redirect, url_for, session, flash
import random
import os
from authlib.integrations.flask_client import OAuth  # pip install flask requests authlib
from dotenv import load_dotenv
from datetime import datetime, timedelta
import socket
import json

HOST = "0.0.0.0"
PORT = 7000

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind((HOST, PORT))
server.listen(1)
print("Server listening...")

conn, addr = server.accept()
print(f"Connected by: {addr}")

while True:
    data = conn.recv(4096)  # adjust buffer size if needed
    if not data:
        break

    # Decode UTF-8
    text = data.decode('utf-8').strip()  # strip removes accidental newlines
    if not text:
        continue

    try:
        features = json.loads(text)
        print("Received Features:")
        for key, value in features.items():
            print(f"{key}: {value}")
    except json.JSONDecodeError as e:
        print("JSON decode error:", e)
        print("Raw data received:", text)

    # Send acknowledgment
    response = json.dumps({"status": "received"})
    conn.sendall(response.encode('utf-8'))

conn.close()
server.close()

mydb = mysql.connector.connect(
    host="localhost",
    user="TouchAlytics",
    password="Touchgroup1!",
    database="touchalytics"
)




mycursor = mydb.cursor()


