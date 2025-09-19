import os
import mysql.connector
from mysql.connector import errorcode

# Database connection config
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "Password1"),
    "database": os.getenv("DB_NAME", "pizza_db"),
    "port": int(os.getenv("DB_PORT", "3306")),
}

def connect():
    try:
        cnx = mysql.connector.connect(**DB_CONFIG)
        return cnx
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("❌ Invalid DB user or password.")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("❌ Database does not exist.")
        else:
            print(f"❌ DB error: {err}")
        raise

def vegan_veg_label(is_vegan, is_vegetarian):
    if is_vegan:
        return " (vegan)"
    if is_vegetarian:
        return " (vegetarian)"
    return ""

def show_menu(cnx):
    print("\n====== MENU ======\n")
    query = """
        SELECT PRODUCT_TYPE_NAME, PRODUCT_NAME, IS_VEGAN, IS_VEGETARIAN, FINAL_PRICE
        FROM v_product_prices
        ORDER BY PRODUCT_TYPE_NAME, PRODUCT_NAME;
    """
    cur = cnx.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    if not rows:
        print("⚠️ No menu items found.")
        return

    current_cat = None
    for cat, name, is_vegan, is_veg, price in rows:
        if cat != current_cat:
            current_cat = cat
            print(f"\n-- {cat.upper()} --")
        label = vegan_veg_label(is_vegan, is_veg)
        print(f"  {name}{label} ... €{price:.2f}")

    cur.close()

def main():
    cnx = connect()
    try:
        while True:
            print("\n=== Pizza App ===")
            print("1) Show full menu")
            print("0) Exit")
            choice = input("> ").strip()
            if choice == "1":
                show_menu(cnx)
            elif choice == "0":
                break
            else:
                print("Unknown choice.")
    finally:
        cnx.close()

if __name__ == "__main__":
    main()
