Created By: Christian Siletti and Clint Grano

The model was trained using a minimum of 50 swipes from each new user. The method we utilized to train the model was SVM.
Once 50 swipes have been recorded, the model will then begin to make predictions as to what user is currently swiping, and determine if the predition and actual match. 
The model is typically has 50%-80% accuracy, however it is very heavily influenced by the uniqueness of the users' swiping pattern. To determine the accuracy the model takes the data and splits it into a training and testing set, split 80:20 respectively. It trains the model based off the 80%, and then tests the model based off the 20%. It then compares the predicted values from the 20% testing and compares them with the actual training values, and returns a report on the models accuracy.
The model only retrains when new users are added or deleted, otherwise it just loads the same model each time.
For every swipe that a user makes, the connection is authenticated and either a valid or an error message is sent to the terminal.
When there is only 1 user present, it returns all swipes as a match due to the model having no other users to compare to.
Some issues that our model encountered were that when no users are in the firebase database, the first user's ID can not be 1, otherwise the database will glitch when trying to read the data into the model.  
It is integral that the Flask program is running for any touch validation to occur.

Resources Used:
https://scikit-learn.org/stable/modules/svm.html
https://www.geeksforgeeks.org/flask-tutorial/

