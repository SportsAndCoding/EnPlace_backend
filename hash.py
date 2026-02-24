import bcrypt
print(bcrypt.hashpw(b'Muchomulla', bcrypt.gensalt()).decode())