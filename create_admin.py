"""
Run this once to create your first admin login:

    python create_admin.py

It will ask for a name, username, and password, then create the account.
Run it again any time to add more admin accounts.
"""
import getpass
from app import create_app
from models import db, Admin

app = create_app()

with app.app_context():
    db.create_all()

    print("=== Create Admin Account ===")
    name = input("Admin name: ").strip()
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ").strip()

    if not (name and username and password):
        print("All fields are required. Aborting.")
        raise SystemExit(1)

    if Admin.query.filter_by(username=username).first():
        print(f"An admin with username '{username}' already exists.")
        raise SystemExit(1)

    admin = Admin(name=name, username=username)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()

    print(f"Admin '{username}' created successfully. You can now log in at /admin/login.")
