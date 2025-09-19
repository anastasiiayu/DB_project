import mysql.connector

# Step 1: Connect to database
conn = mysql.connector.connect(
    host="localhost",      # or "127.0.0.1"
    user="root",  # replace with your MySQL username
    password="Password1",  # replace with your MySQL password
    database="pizza_db"    # the DB you created
)

cursor = conn.cursor()

# Step 2: Run a simple SELECT query
cursor.execute("SELECT PRODUCT_NAME, COST FROM PRODUCT;")

# Step 3: Print results
for row in cursor.fetchall():
    print(row)

# Step 4: Close connection
cursor.close()
conn.close()
