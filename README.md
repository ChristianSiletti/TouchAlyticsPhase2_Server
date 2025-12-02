To run TouchAlytics with a MySQL backend, you first need a MySQL server running on localhost (default port 3306) and a database named touchalytics. After installing MySQL (or using an existing instance), create a dedicated MySQL user account that the Flask app will use. From the MySQL command line or a GUI like MySQL Workbench, connect as a privileged user (often root) and create the application user and database. A typical setup looks like this:

CREATE DATABASE touchalytics CHARACTER SET utf8mb3;

CREATE USER 'TouchAlytics'@'localhost' IDENTIFIED BY 'Touchgroup1!';

GRANT ALL PRIVILEGES ON touchalytics.* TO 'TouchAlytics'@'localhost';

FLUSH PRIVILEGES;


With the database and user created, select the touchalytics database and create the tables in the correct order. The userinfo table must be created first because the swipefeatures table has a foreign key that references userinfo.userID. Still inside MySQL with the touchalytics database selected (via USE touchalytics;), run:

CREATE TABLE userinfo (
  userID int NOT NULL AUTO_INCREMENT,
  email varchar(255) NOT NULL,
  password varchar(255) NOT NULL,
  deviceID varchar(255) NOT NULL,
  PRIMARY KEY (userID)
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb3;


Once userinfo exists, you can create the swipefeatures table that stores all of the numeric swipe features extracted from the Android app. Each row corresponds to a single swipe associated with a given user via the userID foreign key. Execute:

CREATE TABLE swipefeatures (
  userID int NOT NULL,
  strokeDuration decimal(30,16) DEFAULT NULL,
  midStrokeArea decimal(30,16) DEFAULT NULL,
  midStrokePress decimal(30,16) DEFAULT NULL,
  dirEndToEnd decimal(30,16) DEFAULT NULL,
  aveDir decimal(30,16) DEFAULT NULL,
  aveVelo decimal(30,16) DEFAULT NULL,
  pairwiseVeloPercent decimal(30,16) DEFAULT NULL,
  startX decimal(30,16) DEFAULT NULL,
  startY decimal(30,16) DEFAULT NULL,
  stopX decimal(30,16) DEFAULT NULL,
  stopY decimal(30,16) DEFAULT NULL,
  touchArea decimal(30,16) DEFAULT NULL,
  maxVelo decimal(30,16) DEFAULT NULL,
  minVelo decimal(30,16) DEFAULT NULL,
  accel decimal(30,16) DEFAULT NULL,
  decel decimal(30,16) DEFAULT NULL,
  trajLength decimal(30,16) DEFAULT NULL,
  curvature decimal(30,16) DEFAULT NULL,
  veloVariance decimal(30,16) DEFAULT NULL,
  angleChangeRate decimal(30,16) DEFAULT NULL,
  maxPress decimal(30,16) DEFAULT NULL,
  minPress decimal(30,16) DEFAULT NULL,
  initPress decimal(30,16) DEFAULT NULL,
  pressChangeRate decimal(30,16) DEFAULT NULL,
  pressVariance decimal(30,16) DEFAULT NULL,
  maxIdleTime decimal(30,16) DEFAULT NULL,
  straightnessRatio decimal(30,16) DEFAULT NULL,
  xDisplacement decimal(30,16) DEFAULT NULL,
  yDisplacement decimal(30,16) DEFAULT NULL,
  aveTouchArea decimal(30,16) DEFAULT NULL,
  KEY fk_swipefeatures_user (userID),
  CONSTRAINT fk_swipefeatures_user FOREIGN KEY (userID) REFERENCES userinfo (userID) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;


After this, the MySQL side is ready and you can link it to your TouchAlytics backend. The project uses mysql.connector to connect to this database.
The following code can help clarify connections.

auth = Blueprint("auth", __name__)

------------------- MySQL setup -------------------
mydb = mysql.connector.connect(
    host="localhost",
    user="TouchAlytics",
    password="Touchgroup1!",
    database="touchalytics"
)
mycursor = mydb.cursor()
