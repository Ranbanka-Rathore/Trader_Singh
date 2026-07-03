from database_manager import db_manager

try:
    print("Attempting to connect to database...")
    db_manager.connect()
    print("✅ Connection successful!")
    db_manager.initialize_tables()
    print("✅ Tables initialized!")
except Exception as e:
    print(f"❌ Connection failed: {e}")
finally:
    db_manager.close()
