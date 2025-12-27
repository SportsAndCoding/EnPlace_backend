import bcrypt
print(bcrypt.hashpw(b'Baseball#16', bcrypt.gensalt()).decode())