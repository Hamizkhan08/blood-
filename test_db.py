import pymysql

try:
    connection = pymysql.connect(
        host='localhost',
        user='blood_user',
        password='blood_password123',
        database='blood_donation_db'
    )
    print("✅ Database connection successful!")
    connection.close()
except Exception as e:
    print(f"❌ Database connection failed: {e}")