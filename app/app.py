import os
import re
from datetime import datetime
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import errorcode

# -------------------
# DB CONNECTION
# -------------------
load_dotenv()
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "port": int(os.getenv("DB_PORT", "3306")),
}

def connect():
    try:
        cnx = mysql.connector.connect(**DB_CONFIG)
        cnx.autocommit = False  # we'll control transactions manually
        return cnx
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("❌ Invalid DB user or password.")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("❌ Database does not exist.")
        else:
            print(f"❌ DB error: {err}")
        raise

# -------------------
# HELPERS (UI)
# -------------------
def vegan_veg_label(is_vegan, is_vegetarian):
    if is_vegan: return " (vegan)"
    if is_vegetarian: return " (vegetarian)"
    return ""

def show_menu(cnx):
    print("\n====== MENU ======\n")
    query = """
        SELECT PRODUCT_ID, PRODUCT_TYPE_NAME, PRODUCT_NAME, IS_VEGAN, IS_VEGETARIAN, FINAL_PRICE
        FROM v_product_prices
        ORDER BY PRODUCT_TYPE_NAME, PRODUCT_NAME;
    """
    cur = cnx.cursor(dictionary=True)  # use dictionary cursor for readability
    cur.execute(query)
    rows = cur.fetchall()
    if not rows:
        print("⚠️ No menu items found.")
        cur.close()
        return

    current_cat = None
    for row in rows:
        if row["PRODUCT_TYPE_NAME"] != current_cat:
            current_cat = row["PRODUCT_TYPE_NAME"]
            print(f"\n-- {current_cat.upper()} --")
        label = vegan_veg_label(row["IS_VEGAN"], row["IS_VEGETARIAN"])
        print(f"  {row['PRODUCT_ID']}. {row['PRODUCT_NAME']}{label} ... €{row['FINAL_PRICE']:.2f}")
    cur.close()


def extract_postal_prefix(address: str) -> str | None:
    # Try Dutch-style "1234 AB" → take first 4 digits as prefix
    if not address:
        return None
    m = re.search(r'(\d{4})\s*[A-Za-z]{0,2}', address)
    return m.group(1) if m else None

# -------------------
# HELPERS (DB)
# -------------------
def ensure_statuses(cnx):
    """Make sure core statuses exist and return a dict name->id."""
    need = ["CREATED", "IN_PROGRESS", "OUT_FOR_DELIVERY", "DELIVERED"]
    cur = cnx.cursor(dictionary=True)
    cur.execute("SELECT STATUS_ID, STATUS_NAME FROM STATUS")
    have = {r["STATUS_NAME"]: r["STATUS_ID"] for r in cur.fetchall()}
    for name in need:
        if name not in have:
            cur.execute("INSERT INTO STATUS (STATUS_NAME) VALUES (%s)", (name,))
            have[name] = cur.lastrowid
    cnx.commit()
    cur.close()
    return have

def find_customer(cnx, user_input: str):
    """Return customer row by ID or exact name match 'First Last' or by phone."""
    cur = cnx.cursor(dictionary=True)
    if user_input.isdigit():
        cur.execute("SELECT * FROM CUSTOMER WHERE CUSTOMER_ID=%s", (int(user_input),))
    else:
        # try full name exact, then first or last name like
        parts = user_input.strip().split()
        if len(parts) >= 2:
            cur.execute("""SELECT * FROM CUSTOMER
                           WHERE CONCAT(FIRST_NAME,' ',LAST_NAME)=%s""",
                        (' '.join(parts),))
        else:
            cur.execute("""SELECT * FROM CUSTOMER
                           WHERE FIRST_NAME=%s OR LAST_NAME=%s OR PHONE_NUMBER=%s
                           ORDER BY CUSTOMER_ID ASC LIMIT 1""",
                        (user_input, user_input, user_input))
    row = cur.fetchone()
    cur.close()
    return row

def find_product_id(cnx, user_input: str):
    """Return PRODUCT_ID by ID or exact name."""
    cur = cnx.cursor(dictionary=True)
    if user_input.isdigit():
        cur.execute("SELECT PRODUCT_ID FROM PRODUCT WHERE PRODUCT_ID=%s", (int(user_input),))
    else:
        cur.execute("SELECT PRODUCT_ID FROM PRODUCT WHERE PRODUCT_NAME=%s", (user_input,))
    row = cur.fetchone()
    cur.close()
    return row["PRODUCT_ID"] if row else None

def product_price_and_type(cnx, product_id: int):
    cur = cnx.cursor(dictionary=True)
    cur.execute("""SELECT FINAL_PRICE, PRODUCT_TYPE_NAME, PRODUCT_NAME
                   FROM v_product_prices WHERE PRODUCT_ID=%s""", (product_id,))
    row = cur.fetchone()
    cur.close()
    if not row:
        return None, None, None
    return float(row["FINAL_PRICE"]), row["PRODUCT_TYPE_NAME"], row["PRODUCT_NAME"]

def assign_delivery_person(cnx, postal_prefix: str | None):
    """
    Choose an available courier for postal prefix with 30-min cooldown AFTER delivery
    and no active undelivered order. Falls back to any available courier if no prefix match.
    """
    cur = cnx.cursor(dictionary=True)

    # Base WHERE clause for cooldown + undelivered logic
    eligibility_conditions = """
          dp.IS_AVAILABLE = 1
          AND NOT EXISTS (
                SELECT 1 FROM ORDERS o
                WHERE o.DELIVERY_PERSON_ID = dp.DELIVERY_PERSON_ID
                  AND o.DELIVERED_AT IS NOT NULL
                  AND o.DELIVERED_AT > (NOW() - INTERVAL 30 MINUTE)
          )
          AND NOT EXISTS (
                SELECT 1 FROM ORDERS o2
                WHERE o2.DELIVERY_PERSON_ID = dp.DELIVERY_PERSON_ID
                  AND o2.STATUS_ID <> (SELECT STATUS_ID FROM STATUS WHERE STATUS_NAME='DELIVERED' LIMIT 1)
          )
    """

    # 1. Try to match postal prefix
    if postal_prefix:
        cur.execute(f"""
            SELECT dp.DELIVERY_PERSON_ID
            FROM DELIVERY_PERSON dp
            JOIN DELIVERY_PERSON_POSTAL dpp ON dpp.DELIVERY_PERSON_ID = dp.DELIVERY_PERSON_ID
            WHERE dpp.POSTAL_CODE_PREFIX = %s
              AND {eligibility_conditions}
            ORDER BY dp.DELIVERY_PERSON_ID ASC
            LIMIT 1
        """, (postal_prefix,))
        row = cur.fetchone()
        if row:
            cur.close()
            return row["DELIVERY_PERSON_ID"]

    # 2. Fallback: any eligible courier
    cur.execute(f"""
        SELECT dp.DELIVERY_PERSON_ID
        FROM DELIVERY_PERSON dp
        WHERE {eligibility_conditions}
        ORDER BY dp.DELIVERY_PERSON_ID ASC
        LIMIT 1
    """)
    row = cur.fetchone()
    cur.close()
    return row["DELIVERY_PERSON_ID"] if row else None

# -------------------
# FEATURES
# -------------------
def add_customer(cnx):
    print("\n-- Add Customer --")
    first = input("First name: ").strip()
    last  = input("Last name: ").strip()
    bdate = input("Birth date (YYYY-MM-DD): ").strip()
    addr  = input("Address (include postcode, e.g. 'Keizersgracht 12, 1017 AB Amsterdam'): ").strip()
    phone = input("Phone (+31...): ").strip()
    if not first or not last:
        print("⚠️ First and last name are required.")
        return
    try:
        datetime.strptime(bdate, "%Y-%m-%d")
    except Exception:
        print("⚠️ Invalid birth date format.")
        return
    cur = cnx.cursor()
    try:
        cnx.start_transaction()
        cur.execute("""INSERT INTO CUSTOMER(FIRST_NAME, LAST_NAME, BIRTH_DATE, ADDRESS, PHONE_NUMBER, PIZZA_COUNT)
                       VALUES (%s,%s,%s,%s,%s,0)""",
                    (first, last, bdate, addr, phone))
        cnx.commit()
        print("✅ Customer added.")
    except Exception as e:
        cnx.rollback()
        print(f"❌ Failed to add customer: {e}")
    finally:
        cur.close()

def show_customers(cnx):
    cur = cnx.cursor()
    cur.execute("""SELECT CUSTOMER_ID, FIRST_NAME, LAST_NAME, DATE_FORMAT(BIRTH_DATE,'%Y-%m-%d'),
                          ADDRESS, PIZZA_COUNT
                   FROM CUSTOMER ORDER BY CUSTOMER_ID""")
    rows = cur.fetchall()
    if not rows:
        print("⚠️ No customers yet. Add one first.")
    else:
        print("\n-- Customers --")
        for cid, fn, ln, bd, addr, pc in rows:
            print(f"{cid}) {fn} {ln} | DOB {bd} | {addr} | pizzas: {pc}")
    cur.close()

def select_products(cnx):
    """Interactive selection. Returns list[(product_id, amount)]."""
    items = []
    print("\n-- Add items -- (type product ID or exact name; 0 to finish)")
    while True:
        choice = input("Enter product ID or name (0 to finish): ").strip()
        if choice == "0":
            break
        pid = find_product_id(cnx, choice)
        if not pid:
            print("⚠️ Not found. Tip: copy the exact name from menu.")
            continue
        try:
            amt = int(input("Amount: ").strip())
            if amt <= 0:
                print("⚠️ Amount must be > 0.")
                continue
        except ValueError:
            print("⚠️ Amount must be a number.")
            continue
        items.append((pid, amt))
    return items

def place_order(cnx):
    # Ensure statuses exist
    statuses = ensure_statuses(cnx)
    CREATED = statuses["CREATED"]

    # Choose customer
    show_customers(cnx)
    cust_in = input("Enter customer ID or name: ").strip()
    cust = find_customer(cnx, cust_in)
    if not cust:
        print("⚠️ Customer not found.")
        return

    # Select items
    show_menu(cnx)
    items = select_products(cnx)
    if not items:
        print("⚠️ No items selected. Cancelling.")
        return

    # Discounts & delivery prep
    postal_prefix = extract_postal_prefix(cust["ADDRESS"])
    delivery_person_id = assign_delivery_person(cnx, postal_prefix)
    if not delivery_person_id:
        print("⚠️ No available delivery person for your area right now.")
        return

    # Start transaction
    cur = cnx.cursor(dictionary=True)
    try:
        #cnx.start_transaction()not needed

        # Discount inputs/flags
        pizza_count = int(cust["PIZZA_COUNT"] or 0)
        today = datetime.today()
        is_birthday = (cust["BIRTH_DATE"].month == today.month and cust["BIRTH_DATE"].day == today.day)

        code_input = input("Enter discount code (or blank): ").strip()
        discount_id = None
        discount_percent_code = 0.0
        if code_input:
            cur.execute("SELECT DISCOUNT_CODE_ID, DISCOUNT_PERCENT FROM DISCOUNT_CODE WHERE CODE=%s AND IS_USED=0",
                        (code_input,))
            d = cur.fetchone()
            if d:
                discount_id = d["DISCOUNT_CODE_ID"]
                discount_percent_code = float(d["DISCOUNT_PERCENT"])
                # Mark code used now (part of TX)
                cur.execute("UPDATE DISCOUNT_CODE SET IS_USED=1 WHERE DISCOUNT_CODE_ID=%s", (discount_id,))
            else:
                print("⚠️ Invalid or already used code (ignored).")

        # Compute totals + buckets for birthday
        line_items = []  # (name, qty, unit_price, subtotal)
        total_base = 0.0
        pizzas_prices = []
        drinks_prices = []

        for pid, amt in items:
            price, ptype, pname = product_price_and_type(cnx, pid)
            if price is None:
                raise Exception(f"Product {pid} missing in price view.")
            subtotal = price * amt
            total_base += subtotal
            line_items.append((pname, amt, price, subtotal))
            if ptype == "Pizza":
                pizzas_prices.extend([price] * amt)
            elif ptype == "Drink":
                drinks_prices.extend([price] * amt)

        # Apply discounts in spec order: loyalty(10%) -> birthday (free cheapest pizza & drink) -> code
        discounts_applied = []

        # Loyalty: 10% off if customer already has >=10 pizzas BEFORE this order
        if pizza_count >= 10:
            cut = round(total_base * 0.10, 2)
            total_base -= cut
            discounts_applied.append(("Loyalty 10% off", -cut))

        # Birthday: cheapest pizza + cheapest drink free
        if is_birthday:
            if pizzas_prices:
                free_pizza = min(pizzas_prices)
                total_base -= free_pizza
                discounts_applied.append(("Birthday: free pizza", -free_pizza))
            if drinks_prices:
                free_drink = min(drinks_prices)
                total_base -= free_drink
                discounts_applied.append(("Birthday: free drink", -free_drink))

        # Discount code percentage
        if discount_percent_code > 0:
            cut = round(total_base * (discount_percent_code / 100.0), 2)
            total_base -= cut
            discounts_applied.append((f"Code {discount_percent_code:.0f}% off", -cut))

        total_final = max(round(total_base, 2), 0.0)

        # Create order (CREATED)
        cur.execute("""
            INSERT INTO ORDERS (CREATED_AT, TOTAL_PRICE, DISCOUNT_CODE_ID,
                                DELIVERY_PERSON_ID, STATUS_ID, CUSTOMER_ID)
            VALUES (NOW(), %s, %s, %s, %s, %s)
        """, (total_final, discount_id, delivery_person_id, CREATED, cust["CUSTOMER_ID"]))
        order_id = cur.lastrowid

        # Insert order items
        for pid, amt in items:
            cur.execute("""INSERT INTO ORDER_ITEM (AMOUNT, PRODUCT_ID, ORDER_ID)
                           VALUES (%s, %s, %s)""", (amt, pid, order_id))

        # Update pizza count (counts every product of type Pizza)
        pizzas_ordered = sum(amt for (pid, amt) in items
                             if product_price_and_type(cnx, pid)[1] == "Pizza")
        cur.execute("""UPDATE CUSTOMER SET PIZZA_COUNT = PIZZA_COUNT + %s
                       WHERE CUSTOMER_ID=%s""", (pizzas_ordered, cust["CUSTOMER_ID"]))

        # Mark courier unavailable immediately (they’ll be available again when you deliver AND cooldown passes)
        cur.execute("UPDATE DELIVERY_PERSON SET IS_AVAILABLE=0 WHERE DELIVERY_PERSON_ID=%s", (delivery_person_id,))

        cnx.commit()

        # Confirmation printout
        print("\n✅ ORDER CONFIRMATION")
        print(f"Order #{order_id} for {cust['FIRST_NAME']} {cust['LAST_NAME']}")
        print("Items:")
        for name, qty, unit, sub in line_items:
            print(f"  - {name} x{qty} @ €{unit:.2f} = €{sub:.2f}")
        if discounts_applied:
            print("Discounts:")
            for label, val in discounts_applied:
                print(f"  • {label}: €{val:.2f}")
        print(f"TOTAL: €{total_final:.2f}")
        print(f"Assigned courier ID: {delivery_person_id}")
        print("Status: CREATED")

    except Exception as e:
        cnx.rollback()
        print(f"❌ Order failed and rolled back: {e}")
    finally:
        cur.close()

def list_undelivered(cnx):
    statuses = ensure_statuses(cnx)
    DELIVERED = statuses["DELIVERED"]
    cur = cnx.cursor(dictionary=True)
    cur.execute(f"""
        SELECT o.ORDER_ID, o.CREATED_AT, o.TOTAL_PRICE,
               c.FIRST_NAME, c.LAST_NAME,
               dp.FIRST_NAME AS DP_FIRST, dp.LAST_NAME AS DP_LAST,
               s.STATUS_NAME
        FROM ORDERS o
        JOIN CUSTOMER c ON c.CUSTOMER_ID=o.CUSTOMER_ID
        JOIN DELIVERY_PERSON dp ON dp.DELIVERY_PERSON_ID=o.DELIVERY_PERSON_ID
        JOIN STATUS s ON s.STATUS_ID=o.STATUS_ID
        WHERE o.STATUS_ID <> %s
        ORDER BY o.CREATED_AT ASC
    """, (DELIVERED,))
    rows = cur.fetchall()
    if not rows:
        print("\n✅ No undelivered orders.")
    else:
        print("\n-- Undelivered Orders --")
        for r in rows:
            print(f"#{r['ORDER_ID']} | {r['STATUS_NAME']} | €{r['TOTAL_PRICE']:.2f} | "
                  f"{r['FIRST_NAME']} {r['LAST_NAME']} | Courier: {r['DP_FIRST']} {r['DP_LAST']} | "
                  f"Created {r['CREATED_AT']}")
    cur.close()

def mark_delivered(cnx):
    statuses = ensure_statuses(cnx)
    DELIVERED = statuses["DELIVERED"]

    list_undelivered(cnx)
    try:
        oid = int(input("\nEnter ORDER_ID to mark DELIVERED: ").strip())
    except ValueError:
        print("⚠️ Invalid number.")
        return

    cur = cnx.cursor(dictionary=True)
    try:
       # cnx.start_transaction()
        # find order & courier
        cur.execute("SELECT DELIVERY_PERSON_ID FROM ORDERS WHERE ORDER_ID=%s", (oid,))
        row = cur.fetchone()
        if not row:
            print("⚠️ Order not found.")
            cnx.rollback()
            return
        dp_id = row["DELIVERY_PERSON_ID"]

        # mark delivered
        cur.execute("""UPDATE ORDERS
                       SET STATUS_ID=%s, DELIVERED_AT=NOW()
                       WHERE ORDER_ID=%s""", (DELIVERED, oid))

        # check cooldown: courier stays unavailable for 30 minutes after this delivery
        # We won't flip them to available here; instead, availability will be restored manually or
        # you can simulate after 30m by running: UPDATE DELIVERY_PERSON SET IS_AVAILABLE=1 WHERE DELIVERY_PERSON_ID=...
        # For demo convenience, offer to set available if last delivery older than 30m.
        cur.execute("""
            SELECT CASE
                     WHEN NOW() > (SELECT DELIVERED_AT FROM ORDERS WHERE ORDER_ID=%s) + INTERVAL 30 MINUTE
                     THEN 1 ELSE 0
                   END AS can_enable
        """, (oid,))
        can_enable = cur.fetchone()["can_enable"] == 1

        if can_enable:
            cur.execute("UPDATE DELIVERY_PERSON SET IS_AVAILABLE=1 WHERE DELIVERY_PERSON_ID=%s", (dp_id,))
            note = "Courier availability restored (30m passed)."
        else:
            note = "Courier remains unavailable for 30 minutes after delivery."

        cnx.commit()
        print(f"✅ Order #{oid} marked DELIVERED. {note}")
    except Exception as e:
        cnx.rollback()
        print(f"❌ Failed to mark delivered: {e}")
    finally:
        cur.close()

# -------------------
# MAIN LOOP
# -------------------
def main():
    cnx = connect()
    try:
        while True:
            print("\n=== Pizza App ===")
            print("1) Show full menu")
            print("2) Place order")
            print("3) Add customer")
            print("4) Show undelivered orders")
            print("5) Mark order as delivered")
            print("0) Exit")
            choice = input("> ").strip()
            if choice == "1":
                show_menu(cnx)
            elif choice == "2":
                place_order(cnx)
            elif choice == "3":
                add_customer(cnx)
            elif choice == "4":
                list_undelivered(cnx)
            elif choice == "5":
                mark_delivered(cnx)
            elif choice == "0":
                break
            else:
                print("Unknown choice.")
    finally:
        cnx.close()

if __name__ == "__main__":
    main()
