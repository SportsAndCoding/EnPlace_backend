import bcrypt
print(bcrypt.hashpw(b'sales123', bcrypt.gensalt()).decode())